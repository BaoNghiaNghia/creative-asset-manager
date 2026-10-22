"""End-to-end SQL-backed queue contract without calling external image services."""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.ecommerce_pipeline import (
    EcommerceService, EcommerceError,
)
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcEcommerceJobModel,
)
from app.modules.realistic_review_ugc.stage2_skills import ResolvedStage2Skill


@pytest.fixture
def ecommerce_db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        ProcessingJobModel.__table__, TenantProcessingPolicyModel.__table__,
    ])
    import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
    monkeypatch.setattr(pipeline, "_assigned_skill", lambda *args: ResolvedStage2Skill(
        source="local", skill_id=None, skill_name="test-ecommerce-skill", skill_version=None))
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256",
                        lambda *args, **kwargs: "f" * 64)
    from types import SimpleNamespace
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
    )
    with Session(engine) as session:
        session.add(RrugcSourcePlanModel(
            id="plan-001", tenant_id="tenant-001",
            root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="remote-design-file-id",
            source_relative_path="design.png",
            source_name="design.png", source_mime_type="image/png",
            source_revision="rev1", created_by_user_id="operator",
        ))
        session.commit()
        yield session, settings
    engine.dispose()


def test_queue_is_idempotent_and_job_is_durable(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    second = service.queue(tenant_id="tenant-001", user_id="operator",
                           source_plan_id="plan-001", stage="prod_s2_ec1")
    assert first["already_exists"] is False
    assert second["already_exists"] is True
    assert first["job_id"] == second["job_id"]
    rows = list(session.scalars(select(RrugcEcommerceJobModel)))
    assert len(rows) == 1
    assert rows[0].processing_job_id
    assert rows[0].source_revision == "rev1"
    assert len(rows[0].stock_sha256) == 64
    jobs = list(session.scalars(select(ProcessingJobModel)))
    assert len(jobs) == 1 and jobs[0].job_type == "rrugc_ecommerce_generate"
    assert jobs[0].payload_json["ecommerce_job_id"] == first["job_id"]


def test_three_quarter_and_side_require_completed_front(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    for stage in ("prod_s2_ec2", "prod_s2_ec3"):
        with pytest.raises(EcommerceError, match="Skill 1"):
            service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage=stage)


def test_unembroidered_back_has_own_independent_job(ecommerce_db):
    session, settings = ecommerce_db
    result = EcommerceService(session, settings).queue(
        tenant_id="tenant-001", user_id="operator",
        source_plan_id="plan-001", stage="prod_s2_ec4")
    assert result["status"] == "queued"





def test_retry_creates_a_new_processing_attempt_without_duplicating_slot(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, first["job_id"])
    processing = session.get(ProcessingJobModel, job.processing_job_id)
    job.status = "failed"
    processing.status = "failed"
    session.commit()
    original_processing = job.processing_job_id
    result = service.retry(tenant_id="tenant-001", job_id=job.id)
    assert result["retry_count"] == 1 and result["status"] == "queued"
    assert job.processing_job_id != original_processing
    assert session.scalar(select(__import__("sqlalchemy").func.count()).select_from(
        ProcessingJobModel)) == 2
    with pytest.raises(EcommerceError, match="Only failed"):
        service.retry(tenant_id="tenant-001", job_id=job.id)


def test_regenerate_retains_previous_output_and_queues_new_attempt(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, first["job_id"])
    job.status = "completed"
    job.output_remote_file_id = "previous-image"
    session.commit()
    old_processing = job.processing_job_id
    result = service.regenerate(tenant_id="tenant-001", job_id=job.id)
    assert result["status"] == "queued"
    assert job.output_remote_file_id == "previous-image"
    assert job.processing_job_id != old_processing


def test_angle_stage_can_queue_after_front_output_persisted(ecommerce_db):
    session, settings = ecommerce_db
    service = EcommerceService(session, settings)
    first = service.queue(tenant_id="tenant-001", user_id="operator",
                          source_plan_id="plan-001", stage="prod_s2_ec1")
    job = session.get(RrugcEcommerceJobModel, first["job_id"])
    job.status = "completed"
    job.output_remote_file_id = "front-remote"
    session.commit()
    for stage in ("prod_s2_ec2", "prod_s2_ec3"):
        result = service.queue(tenant_id="tenant-001", user_id="operator",
                               source_plan_id="plan-001", stage=stage)
        assert result["status"] == "queued" and result["already_exists"] is False
        child = session.get(RrugcEcommerceJobModel, result["job_id"])
        assert child.front_reference_remote_file_id == "front-remote"


def test_other_tenant_cannot_queue_same_plan(ecommerce_db):
    session, settings = ecommerce_db
    with pytest.raises(EcommerceError):
        EcommerceService(session, settings).queue(
            tenant_id="other-tenant", user_id="actor",
            source_plan_id="plan-001", stage="prod_s2_ec1")
