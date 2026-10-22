from __future__ import annotations

from test_skill_registry import _engine, _catalog_item

from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc import skill_registry, stage2_skills
from app.modules.realistic_review_ugc.model import RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel

def test_existing_tenant_discovers_new_bundled_ecommerce_skills_without_overwriting_assignments(monkeypatch):
    from dataclasses import replace
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
            manifest = object()
            name = "gatorhats-8869-ecommerce-front"
            monkeypatch.setattr(stage2_skills, "_local_manifests", lambda settings: {name: manifest})
            monkeypatch.setattr(stage2_skills, "_local_item", lambda settings, m:
                                replace(_catalog_item(), skill_name=name, display_name="Ecommerce Front"))
            monkeypatch.setattr(skill_registry, "installed_stage2_skill_sha256",
                                lambda name, **kwargs: "a" * 64)
            monkeypatch.setattr(skill_registry, "_sync_changed_local_skills", lambda *a, **kw: None)
            first = skill_registry.ensure_skill_registry(session, tenant_id="tenant-a")
            assert {row.skill_name for row in first} == {"existing-studio", name}
            second = skill_registry.ensure_skill_registry(session, tenant_id="tenant-a")
            assert {row.id for row in first} == {row.id for row in second}
            assert session.get(RrugcStageSkillDefaultModel, ("tenant-a", "prod_s2_ec1")).registry_id == existing.id
            assert session.get(RrugcStageSkillDefaultModel, ("tenant-a", "prod_s2_ec2")).registry_id is None
    finally:
        engine.dispose()
