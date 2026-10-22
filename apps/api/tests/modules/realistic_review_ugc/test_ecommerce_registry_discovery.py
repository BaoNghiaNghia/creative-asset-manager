from __future__ import annotations

from dataclasses import replace
from sqlalchemy.orm import Session
from test_skill_registry import _engine, _catalog_item
from app.modules.realistic_review_ugc import skill_registry, stage2_skills
from app.modules.realistic_review_ugc.model import RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel

NAMES = {
    "prod_s2_ec1": "gatorhats-8869-ecommerce-front",
    "prod_s2_ec2": "gatorhats-8869-ecommerce-three-quarter",
    "prod_s2_ec3": "gatorhats-8869-ecommerce-right-side",
    "prod_s2_ec4": "gatorhats-8869-ecommerce-back",
}

def test_existing_tenant_discovers_and_assigns_only_untouched_ecommerce_slots(monkeypatch):
    engine = _engine()
    try:
        with Session(engine) as session:
            existing = RrugcStage2SkillRegistryModel(
                tenant_id="tenant-a", source="local", skill_name="existing-studio",
                display_name="Existing Studio", enabled=True,
            )
            session.add(existing)
            session.flush()
            session.add(RrugcStageSkillDefaultModel(
                tenant_id="tenant-a", stage="prod_s2_ec1", registry_id=existing.id))
            session.add(RrugcStageSkillDefaultModel(
                tenant_id="tenant-a", stage="prod_s2_ec2", registry_id=None))
            session.commit()
            monkeypatch.setattr(stage2_skills, "_local_manifests",
                                lambda settings: {name: object() for name in NAMES.values()})
            def _item(settings, manifest):
                # The test needs a stable name; use a unique sentinel for each manifest.
                return replace(_catalog_item(), skill_name=manifest,
                               display_name=manifest)
            manifests = {name: name for name in NAMES.values()}
            monkeypatch.setattr(stage2_skills, "_local_manifests", lambda settings: manifests)
            monkeypatch.setattr(stage2_skills, "_local_item", _item)
            monkeypatch.setattr(skill_registry, "installed_stage2_skill_sha256",
                                lambda name, **kwargs: "a" * 64)
            monkeypatch.setattr(skill_registry, "_sync_changed_local_skills",
                                lambda *args, **kwargs: None)
            first = skill_registry.ensure_skill_registry(session, tenant_id="tenant-a")
            assert {name for name in NAMES.values()} <= {row.skill_name for row in first}
            assert session.get(RrugcStageSkillDefaultModel, ("tenant-a", "prod_s2_ec1")).registry_id == existing.id
            assert session.get(RrugcStageSkillDefaultModel, ("tenant-a", "prod_s2_ec2")).registry_id is None
            for stage in ("prod_s2_ec3", "prod_s2_ec4"):
                row = session.get(RrugcStageSkillDefaultModel, ("tenant-a", stage))
                assert row.registry_id
                assert session.get(RrugcStage2SkillRegistryModel, row.registry_id).skill_name == NAMES[stage]
            second = skill_registry.ensure_skill_registry(session, tenant_id="tenant-a")
            assert {row.id for row in first} == {row.id for row in second}
    finally:
        engine.dispose()
