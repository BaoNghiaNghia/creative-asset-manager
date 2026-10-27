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

