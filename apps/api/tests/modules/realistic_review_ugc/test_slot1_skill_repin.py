"""Explicit regeneration/retry adopts Thread v2 without altering historical rows."""
from types import SimpleNamespace

from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline


def _old_job(stage="prod_s2_ec1"):
    return SimpleNamespace(
        stage=stage, skill_source="local", skill_id=None,
        skill_name="8869-ecom-front-fine-thread-v1",
        skill_version=None, skill_bundle_sha256="old-hash",
    )


def test_explicit_slot1_can_adopt_thread_v2(monkeypatch):
    row = _old_job()
    selection = SimpleNamespace(
        source="local", skill_id=None,
        skill_name="gatorhats-8869-ecommerce-front-thread-v2",
        skill_version=None,
    )
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: selection)
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256", lambda *args, **kwargs: "v2-hash")
    assert pipeline._repin_explicit_slot1_to_thread_v2(None, None, "tenant", row)
    assert row.skill_name == selection.skill_name
    assert row.skill_bundle_sha256 == "v2-hash"


def test_explicit_slot1_regeneration_adopts_prompt_first_v4(monkeypatch):
    row = _old_job()
    selected = SimpleNamespace(
        source="local", skill_id=None,
        skill_name="gatorhats-8869-ecommerce-front-thread-v4",
        skill_version="4.0.0",
    )
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: selected)
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256",
                        lambda *args, **kwargs: "v4-pinned-hash")
    assert pipeline._repin_explicit_slot1_to_thread_v2(None, None, "tenant", row)
    assert row.skill_name.endswith("-v4")
    assert row.skill_version == "4.0.0"
    assert row.skill_bundle_sha256 == "v4-pinned-hash"


def test_explicit_slot1_regeneration_adopts_threadtransfer_lite_without_touching_history(monkeypatch):
    row = _old_job()
    selected = SimpleNamespace(
        source="local", skill_id=None,
        skill_name="gatorhats-8869-slot1-threadtransfer-lite-v1",
        skill_version="1.0.0",
    )
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: selected)
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256",
                        lambda *args, **kwargs: "lite-immutable-hash")
    assert pipeline._repin_explicit_slot1_to_thread_v2(None, None, "tenant", row)
    assert row.skill_name == selected.skill_name
    assert row.skill_version == "1.0.0"
    assert row.skill_bundle_sha256 == "lite-immutable-hash"


def test_no_other_slot_is_rebound(monkeypatch):
    row = _old_job("prod_s2_ec2")
    monkeypatch.setattr(pipeline, "_assigned_skill",
                        lambda *args: (_ for _ in ()).throw(AssertionError("should not resolve")))
    assert not pipeline._repin_explicit_slot1_to_thread_v2(None, None, "tenant", row)
    assert row.skill_name == "8869-ecom-front-fine-thread-v1"
    assert row.skill_bundle_sha256 == "old-hash"


def test_previous_slot1_default_does_not_force_switch(monkeypatch):
    row = _old_job()
    old_selection = SimpleNamespace(
        source="local", skill_id=None,
        skill_name="8869-ecom-front-fine-thread-v1", skill_version=None,
    )
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: old_selection)
    assert not pipeline._repin_explicit_slot1_to_thread_v2(None, None, "tenant", row)
    assert row.skill_bundle_sha256 == "old-hash"


def test_explicit_retry_can_adopt_ai_material_v3(monkeypatch):
    row = _old_job()
    selected = SimpleNamespace(
        source="local", skill_id=None,
        skill_name="gatorhats-8869-ecommerce-front-thread-v3",
        skill_version=None,
    )
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: selected)
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256", lambda *args, **kwargs: "v3-hash")
    assert pipeline._repin_explicit_slot1_to_thread_v2(None, None, "tenant", row)
    assert row.skill_name.endswith("-v3")
    assert row.skill_bundle_sha256 == "v3-hash"


def test_slot1_lite_refreshes_stale_deployed_skill_metadata_without_committing(monkeypatch):
    """Explicit regenerate after a deploy pins new SKILL.md, not stale DB v1.0."""
    from app.modules.realistic_review_ugc import skill_registry, stage2_skills
    from app.modules.realistic_review_ugc.model import (
        RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
    )

    selected = SimpleNamespace(
        tenant_id="tenant-a", source="local", skill_id=None,
        skill_name="gatorhats-8869-slot1-threadtransfer-lite-v1",
        synced_version="1.0.0", default_version="1.0.0",
        bundle_sha256="old-hash", enabled=True, deleted_at=None,
        validation_status="valid",
    )
    assignment = SimpleNamespace(registry_id="row-1")
    calls = []
    class Session:
        def get(self, model, key):
            return assignment if model is RrugcStageSkillDefaultModel else selected
        def commit(self):
            raise AssertionError("Selection must not commit a locked Ecommerce transaction")

    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256",
                        lambda *_args, **_kwargs: "new-hash")
    monkeypatch.setattr(stage2_skills, "_local_manifests",
                        lambda _settings: {selected.skill_name: object()})
    monkeypatch.setattr(stage2_skills, "_local_item",
                        lambda _settings, _manifest: SimpleNamespace(ready=True))
    def fake_upsert(session, *, tenant_id, actor_id, item):
        assert tenant_id == "tenant-a" and actor_id is None and item.ready
        calls.append("updated")
        selected.synced_version = "1.1.0"
        selected.default_version = "1.1.0"
        selected.bundle_sha256 = "new-hash"
    monkeypatch.setattr(skill_registry, "_upsert_catalog_item", fake_upsert)
    monkeypatch.setattr(pipeline, "resolve_stage2_skill",
                        lambda **kwargs: SimpleNamespace(
                            source=kwargs["skill_source"], skill_id=kwargs["skill_id"],
                            skill_name=kwargs["skill_name"], skill_version=kwargs["skill_version"],
                        ))
    monkeypatch.setattr(pipeline, "assert_skill_enabled", lambda *_args, **_kwargs: None)
    session = Session()
    settings = SimpleNamespace()
    resolved = pipeline._assigned_skill(session, settings, "tenant-a", "prod_s2_ec1")
    assert resolved.skill_version == "1.1.0"
    assert calls == ["updated"]
    previous = _old_job()
    assert pipeline._repin_explicit_slot1_to_thread_v2(session, settings, "tenant-a", previous)
    assert previous.skill_version == "1.1.0"
    assert previous.skill_bundle_sha256 == "new-hash"
    assert calls == ["updated"]
