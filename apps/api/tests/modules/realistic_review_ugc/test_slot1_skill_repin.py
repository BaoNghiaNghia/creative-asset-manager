"""Stage 2 Slot 1 uses an immutable built-in pipeline on explicit rerun."""
from types import SimpleNamespace

from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
from app.providers.ai.codex_image import (
    SLOT1_DIRECT_NAME, SLOT1_DIRECT_SHA256, SLOT1_DIRECT_VERSION,
)


def _old_job(stage="prod_s2_ec1"):
    return SimpleNamespace(
        stage=stage, skill_source="local", skill_id=None,
        skill_name="gatorhats-8869-slot1-threadtransfer-lite-v1",
        skill_version="1.3.0", skill_bundle_sha256="old-hash",
    )


def test_builtin_slot1_selection_does_not_resolve_registry(monkeypatch):
    monkeypatch.setattr(
        pipeline, "_assigned_skill",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Slot 1 must not load custom Skill registry")
        ),
    )
    result = pipeline._slot1_direct_selection()
    assert result.source == "internal"
    assert result.skill_name == SLOT1_DIRECT_NAME
    assert result.skill_version == SLOT1_DIRECT_VERSION
    assert pipeline._skill_snapshot_sha(result, None) == SLOT1_DIRECT_SHA256


def test_explicit_regenerate_replaces_historical_skill_reference_only(monkeypatch):
    row = _old_job()
    monkeypatch.setattr(
        pipeline, "_assigned_skill",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Regenerate must not require an uploaded Skill")
        ),
    )
    assert pipeline._repin_slot1_direct(row)
    assert row.skill_source == "internal"
    assert row.skill_id is None
    assert row.skill_name == SLOT1_DIRECT_NAME
    assert row.skill_version == SLOT1_DIRECT_VERSION
    assert row.skill_bundle_sha256 == SLOT1_DIRECT_SHA256


def test_non_front_job_stays_pinned_to_its_previous_skill():
    row = _old_job(stage="prod_s2_ec2")
    assert not pipeline._repin_slot1_direct(row)
    assert row.skill_name == "gatorhats-8869-slot1-threadtransfer-lite-v1"
    assert row.skill_bundle_sha256 == "old-hash"


def test_other_skill_hash_is_still_verified(monkeypatch):
    row = SimpleNamespace(source="local", skill_name="gatorhats-8869-ugc-v3")
    monkeypatch.setattr(
        pipeline, "installed_stage2_skill_sha256",
        lambda *args, **kwargs: "original-sha",
    )
    assert pipeline._skill_snapshot_sha(row, None) == "original-sha"
