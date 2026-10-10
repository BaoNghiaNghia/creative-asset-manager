"""Safety regression coverage for a narrowly scoped production Stage 1 reset."""
from datetime import datetime, timezone
from dataclasses import asdict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.keyword_images import KeywordImageService
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel, RrugcKeywordVolumeModel,
)
from app.operations.reset_stage1_keyword_outputs import TARGETS, inspect_candidates, reset_candidates, verify_reset


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for table in (
        RrugcKeywordVolumeModel.__table__,
        RrugcKeywordImageJobModel.__table__,
        RrugcImageOutputVersionModel.__table__,
        ProcessingJobModel.__table__,
    ):
        table.create(engine)
    with Session(engine) as session:
        for index, (name, volume) in enumerate(TARGETS):
            kid, jid, pid = f"keyword-{index}", f"job-{index}", f"processing-{index}"
            session.add(RrugcKeywordVolumeModel(
                id=kid, tenant_id="tenant-a", keyword=name,
                keyword_normalized=name.lower(), search_volume=volume,
                picked=True, provider="test", created_at=datetime.now(timezone.utc),
            ))
            session.add(RrugcKeywordImageJobModel(
                id=jid, tenant_id="tenant-a", keyword_id=kid,
                keyword_text=name, skill_source="local", skill_name="skill",
                prompt_text="prompt", status="completed", created_by_user_id="actor",
                processing_job_id=pid, output_remote_file_id=f"drive-{index}",
            ))
            session.add(ProcessingJobModel(
                id=pid, tenant_id="tenant-a", job_type="rrugc_keyword_image_generate",
                entity_type="rrugc_keyword_image_job", entity_id=jid,
                idempotency_key=f"test-{index}",
                payload_json={"keyword_image_job_id": jid}, status="completed",
                max_attempts=1,
            ))
            session.add(RrugcImageOutputVersionModel(
                id=f"version-{index}", tenant_id="tenant-a",
                stage="stage1", job_id=jid, version=1,
                remote_file_id=f"drive-{index}",
            ))
        session.commit()
        yield session
    engine.dispose()


def test_reset_only_detaches_three_completed_jobs_and_preserves_history(db):
    candidates = inspect_candidates(db)
    assert [c.keyword for c in candidates] == [item[0] for item in TARGETS]
    verified = reset_candidates(db, expected_job_ids={c.job_id for c in candidates})
    db.commit()
    assert len(verified) == 3
    assert db.query(RrugcKeywordImageJobModel).count() == 0
    assert db.query(RrugcImageOutputVersionModel).count() == 3
    assert db.query(ProcessingJobModel).count() == 3
    page = KeywordImageService(db).list_used(tenant_id="tenant-a", page=1, page_size=20)
    assert page["total"] == 3
    assert all(item["status"] == "not_run" and item["job_id"] is None
               and item["output_url"] is None and item["saved_output_count"] == 0
               for item in page["items"])


def test_reset_recovers_legacy_output_pointer_before_detaching_job(db):
    legacy = db.get(RrugcImageOutputVersionModel, "version-2")
    db.delete(legacy)
    db.commit()
    candidates = inspect_candidates(db)
    assert candidates[2].version_count == 0
    reset_candidates(db, expected_job_ids={c.job_id for c in candidates})
    db.commit()
    recovered = db.query(RrugcImageOutputVersionModel).filter_by(job_id="job-2").one()
    assert recovered.version == 1
    assert recovered.remote_file_id == "drive-2"
    assert recovered.processing_job_id == "processing-2"
    assert db.query(RrugcKeywordImageJobModel).count() == 0


def test_verify_reset_checks_actual_live_state_against_recovery_manifest(db):
    candidates = inspect_candidates(db)
    manifest = {"candidates": [asdict(row) for row in candidates]}
    reset_candidates(db, expected_job_ids={c.job_id for c in candidates})
    db.commit()
    assert verify_reset(db, manifest) == ["beach please", "BEACH Life", "Dear Sunday"]


def test_reset_rejects_changed_job_ids_without_modification(db):
    with pytest.raises(RuntimeError, match="Job IDs changed"):
        reset_candidates(db, expected_job_ids={"job-0", "job-1", "wrong"})
    db.rollback()
    assert db.query(RrugcKeywordImageJobModel).count() == 3


@pytest.mark.parametrize("status", ["pending", "processing", "retry"])
def test_reset_refuses_non_terminal_processing(db, status):
    db.get(ProcessingJobModel, "processing-2").status = status
    db.commit()
    with pytest.raises(RuntimeError, match="processing still active"):
        inspect_candidates(db)
    assert db.query(RrugcKeywordImageJobModel).count() == 3


def test_reset_refuses_additional_output_versions(db):
    db.add(RrugcImageOutputVersionModel(
        id="extra", tenant_id="tenant-a", stage="stage1",
        job_id="job-1", version=2, remote_file_id="drive-extra",
    ))
    db.commit()
    with pytest.raises(RuntimeError, match="expected zero or one matching saved image"):
        inspect_candidates(db)


def test_reset_refuses_ambiguous_tenants(db):
    db.add(RrugcKeywordVolumeModel(
        id="keyword-elsewhere", tenant_id="tenant-b", keyword=TARGETS[0][0],
        keyword_normalized="beach please", search_volume=140,
        picked=True, provider="test",
    ))
    db.add(RrugcKeywordImageJobModel(
        id="job-elsewhere", tenant_id="tenant-b", keyword_id="keyword-elsewhere",
        keyword_text=TARGETS[0][0], skill_source="local", skill_name="skill",
        prompt_text="prompt", status="completed", created_by_user_id="actor",
        processing_job_id="processing-elsewhere", output_remote_file_id="extra-drive",
    ))
    db.commit()
    with pytest.raises(RuntimeError, match="expected exactly one"):
        inspect_candidates(db)
