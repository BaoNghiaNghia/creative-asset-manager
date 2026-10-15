"""Stage 4 card regenerate must preserve immutable inputs and avoid duplicate queueing."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel, RrugcStage2JobModel
from app.modules.realistic_review_ugc.stage2 import RrugcStage2Error, RrugcStage2Service


@pytest.fixture()
def generation(monkeypatch):
    from app.modules.realistic_review_ugc import stage2
    monkeypatch.setattr(stage2, "_require_stage4_storage", lambda _settings: None)
    monkeypatch.setattr(stage2, "ensure_skill_registry", lambda *args, **kwargs: None)
    monkeypatch.setattr(stage2, "assert_skill_enabled", lambda *args, **kwargs: None)
    monkeypatch.setattr(stage2, "resolve_stage2_skill", lambda **kwargs: SimpleNamespace(
        source="local", skill_name="qa-stage4-skill", skill_id=None,
        skill_version="20_00_15_10_26" if kwargs["skill_version"] is None else kwargs["skill_version"],
    ))
    expected_hash = "a" * 64
    monkeypatch.setattr(stage2, "installed_stage2_skill_sha256",
                        lambda *args, **kwargs: expected_hash)
    engine = create_engine("sqlite://")
    for model in (
        TenantProcessingPolicyModel, ProcessingJobModel,
        RrugcSourcePlanModel, RrugcStage2JobModel,
    ):
        model.__table__.create(engine)
    with Session(engine) as session:
        plan = RrugcSourcePlanModel(
            id=str(uuid4()), tenant_id="tenant-a", campaign_id=str(uuid4()),
            source_file_id="original-hat-file", source_revision="source-v1",
            source_name="Hat.png", source_relative_path="Hat.png",
            root_folder_id="root", source_mime_type="image/png",
            status="ready", created_by_user_id="author",
        )
        session.add(plan)
        session.flush()
        source = {
            "source_plan_id": plan.id, "source_revision": plan.source_revision,
            "remote_file_id": "original-hat-file", "content_type": "image/png",
        }
        references = [{
            "candidate_id": "pin-1", "remote_file_id": "pinterest-source-1",
            "image_url": "https://example.test/source.jpg",
        }]
        original = RrugcStage2JobModel(
            id=str(uuid4()), tenant_id="tenant-a", source_plan_id=plan.id,
            campaign_id=plan.campaign_id, source_revision=plan.source_revision,
            skill_name="qa-stage4-skill", skill_source="local",
            skill_version="18_39_12_10_26", skill_bundle_sha256=expected_hash,
            selected_candidate_ids_json=["pin-1"],
            selected_reference_snapshot_json=references,
            selected_source_snapshot_json=source,
            prompt_text="Original cap and contextual lighting",
            status="completed", idempotency_key="original-output",
            output_remote_file_id="original-generated-output",
            output_remote_folder_id="original-folder",
            created_by_user_id="author", completed_at=datetime.now(timezone.utc),
        )
        session.add(original)
        session.commit()
        yield session, original, plan, monkeypatch, stage2
    engine.dispose()


def test_regeneration_keeps_all_outputs_and_saved_skill_snapshot(generation):
    session, original, plan, _patch, _module = generation
    service = RrugcStage2Service(session)
    created = service.regenerate_completed_job(
        tenant_id="tenant-a", user_id="operator", job_id=original.id,
    )
    assert created.id != original.id
    assert created.regenerated_from_job_id == original.id
    assert created.status == "queued"
    assert created.selected_source_snapshot_json == original.selected_source_snapshot_json
    assert created.selected_reference_snapshot_json == original.selected_reference_snapshot_json
    assert created.selected_candidate_ids_json == original.selected_candidate_ids_json
    assert created.prompt_text == original.prompt_text
    assert created.skill_version == "20_00_15_10_26"
    assert created.skill_bundle_sha256 == original.skill_bundle_sha256
    assert created.processing_job_id is not None
    assert session.get(RrugcStage2JobModel, original.id).output_remote_file_id == "original-generated-output"
    assert session.get(ProcessingJobModel, created.processing_job_id).job_type == "rrugc_stage2_generate"
    # A duplicate request to either the root or new branch reuses the single
    # active queued job; the backend must enforce it, not just disable a button.
    repeat = service.regenerate_completed_job(
        tenant_id="tenant-a", user_id="operator", job_id=original.id,
    )
    assert repeat.id == created.id
    assert len(list(session.scalars(select(RrugcStage2JobModel)))) == 2
    created.status = "completed"
    created.output_remote_file_id = "new-generated-output"
    session.commit()
    next_version = service.regenerate_completed_job(
        tenant_id="tenant-a", user_id="operator", job_id=created.id,
    )
    assert next_version.regenerated_from_job_id == original.id
    assert next_version.id not in (created.id, original.id)
    assert {r.output_remote_file_id for r in session.scalars(
        select(RrugcStage2JobModel).where(RrugcStage2JobModel.status == "completed")
    )} == {"original-generated-output", "new-generated-output"}


def test_regeneration_uses_latest_skill_but_refuses_changed_source(generation):
    session, original, plan, monkeypatch, module = generation
    service = RrugcStage2Service(session)
    monkeypatch.setattr(module, "installed_stage2_skill_sha256",
                        lambda *args, **kwargs: "b" * 64)
    created = service.regenerate_completed_job(
        tenant_id="tenant-a", user_id="operator", job_id=original.id,
    )
    assert created.skill_bundle_sha256 == "b" * 64
    assert created.skill_version == "20_00_15_10_26"
    assert original.skill_bundle_sha256 == "a" * 64
    created.status = "completed"
    created.output_remote_file_id = "regenerated-output"
    plan.source_revision = "changed"
    session.commit()
    with pytest.raises(RrugcStage2Error) as changed_source:
        service.regenerate_completed_job(
            tenant_id="tenant-a", user_id="operator", job_id=original.id,
        )
    assert changed_source.value.code == "stage4_source_changed"
    assert len(list(session.scalars(select(RrugcStage2JobModel)))) == 2