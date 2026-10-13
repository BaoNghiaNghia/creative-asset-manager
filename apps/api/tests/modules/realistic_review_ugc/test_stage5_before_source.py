"""Stage 5 must compare the exact input photo to its generated output."""
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcStage2JobModel,
)
from app.modules.realistic_review_ugc.stage5_before_source import stage5_before_source


def job(*, tenant="tenant-a", revision="same", snapshot=None):
    return RrugcStage2JobModel(
        tenant_id=tenant, source_plan_id="plan-a", campaign_id="campaign",
        source_revision=revision, skill_name="skill",
        selected_candidate_ids_json=[], selected_reference_snapshot_json=[],
        selected_source_snapshot_json=snapshot, idempotency_key="test",
        created_by_user_id="operator",
    )


def plan(*, tenant="tenant-a", revision="same", file_id="latest"):
    return RrugcSourcePlanModel(
        id="plan-a", tenant_id=tenant, source_file_id=file_id,
        source_revision=revision, source_name="Hat.png",
        source_mime_type="image/png", source_size_bytes=123,
        root_folder_id="root", source_relative_path="Hat.png",
        created_by_user_id="operator",
    )


def test_source_snapshot_is_authoritative_even_when_plan_changed():
    source = job(snapshot={
        "remote_file_id": "hat-used-at-generation", "content_type": "image/jpeg",
        "size_bytes": 234,
    })
    assert stage5_before_source(source, plan(revision="new", file_id="different")) == (
        "hat-used-at-generation", "image/jpeg", 234,
    )


def test_legacy_before_only_when_revision_and_tenant_still_match():
    assert stage5_before_source(job(), plan()) == ("latest", "image/png", 123)
    assert stage5_before_source(job(), plan(revision="new")) is None
    assert stage5_before_source(job(), plan(tenant="tenant-b")) is None
    assert stage5_before_source(job(), plan(file_id=None)) is None
    assert stage5_before_source(job(snapshot={"remote_file_id": ""}), plan()) is None
