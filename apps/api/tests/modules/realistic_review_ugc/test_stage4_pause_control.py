"""Stage 4 Pause/Resume is durable, tenant-scoped and does not touch jobs."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.claim import TenantAwareJobClaimer
from app.modules.processing_policy.model import ProcessingPolicyAuditModel
from app.modules.realistic_review_ugc.model import (
    RrugcStage2JobModel, RrugcStage4QueueControlModel,
)
from app.modules.realistic_review_ugc.stage4_queue_control import (
    set_stage4_paused, stage4_queue_status,
)


def test_stage4_pause_gate_preserves_jobs_and_does_not_pause_other_tenants_or_lanes():
    engine = create_engine("sqlite://")
    for table in (ProcessingJobModel.__table__, RrugcStage2JobModel.__table__,
                  RrugcStage4QueueControlModel.__table__,
                  ProcessingPolicyAuditModel.__table__):
        table.create(engine)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        stage_id = str(uuid4())
        other_id = str(uuid4())
        tenant_b_id = str(uuid4())
        for job_id, tenant, job_type in (
            (stage_id, "tenant-a", "rrugc_stage2_generate"),
            (other_id, "tenant-a", "rrugc_keyword_image_generate"),
            (tenant_b_id, "tenant-b", "rrugc_stage2_generate"),
        ):
            session.add(ProcessingJobModel(
                id=job_id, tenant_id=tenant, job_type=job_type,
                entity_type="test", entity_id=job_id,
                idempotency_key=job_id, payload_json={},
                status="pending", next_attempt_at=now,
            ))
        session.add(RrugcStage2JobModel(
            id=str(uuid4()), tenant_id="tenant-a", source_plan_id=str(uuid4()),
            campaign_id=str(uuid4()), source_revision="1", skill_name="skill",
            selected_candidate_ids_json=[], selected_reference_snapshot_json=[],
            status="queued", idempotency_key="stage4-test", processing_job_id=stage_id,
            created_by_user_id="user", queued_at=now,
        ))
        session.commit()
        assert stage4_queue_status(session, "tenant-a") == {
            "paused": False, "queued": 1, "running": 0,
        }
        result = set_stage4_paused(
            session, tenant_id="tenant-a", user_id="operator", paused=True,
        )
        assert result == {"paused": True, "queued": 1, "running": 0}
        allowed = set(session.scalars(select(ProcessingJobModel.id).where(
            TenantAwareJobClaimer._stage4_available()
        )))
        assert stage_id not in allowed
        assert other_id in allowed
        assert tenant_b_id in allowed
        assert session.get(ProcessingJobModel, stage_id).status == "pending"
        assert session.query(ProcessingPolicyAuditModel).count() == 1
        # Idempotent repeat should not add an audit event.
        set_stage4_paused(session, tenant_id="tenant-a", user_id="operator", paused=True)
        assert session.query(ProcessingPolicyAuditModel).count() == 1
        set_stage4_paused(session, tenant_id="tenant-a", user_id="operator", paused=False)
        allowed = set(session.scalars(select(ProcessingJobModel.id).where(
            TenantAwareJobClaimer._stage4_available()
        )))
        assert stage_id in allowed
        assert session.query(ProcessingPolicyAuditModel).count() == 2
    engine.dispose()
