from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcScoutQueryModel, RrugcScoutRunModel, RrugcScoutMetricCycleModel,
    RrugcKeywordVolumeModel, RrugcScoutFeedbackModel,
)
from app.modules.realistic_review_ugc.scout_dashboard import scout_jobs_snapshot
from app.modules.realistic_review_ugc.service import ANALYZE_JOB_TYPE, IMPORT_JOB_TYPE


def test_scout_jobs_snapshot_tenant_and_agent_isolation():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = (
        ProcessingJobModel.__table__,
        RrugcScoutRunModel.__table__,
        RrugcScoutQueryModel.__table__,
        RrugcScoutMetricCycleModel.__table__,
        RrugcKeywordVolumeModel.__table__,
        RrugcScoutFeedbackModel.__table__,
    )
    for table in tables:
        table.create(engine)
    now = datetime.now(timezone.utc)
    try:
        with Session(engine) as session:
            for tenant, agent, status in [
                ("tenant-a", "agent-a", "running"),
                ("tenant-a", "agent-a", "completed"),
                ("tenant-b", "agent-b", "failed"),
            ]:
                session.add(RrugcScoutRunModel(
                    tenant_id=tenant, agent_id=agent, campaign_id="campaign-" + tenant,
                    status=status, query="vintage embroidered hat",
                    target_count=8, max_scroll_batches=2, auto_import=True,
                    updated_at=now,
                ))
            for i, (tenant, job_type, status) in enumerate([
                ("tenant-a", ANALYZE_JOB_TYPE, "pending"),
                ("tenant-a", ANALYZE_JOB_TYPE, "retry"),
                ("tenant-a", ANALYZE_JOB_TYPE, "processing"),
                ("tenant-a", IMPORT_JOB_TYPE, "completed"),
                ("tenant-a", ANALYZE_JOB_TYPE, "failed"),
                ("tenant-b", ANALYZE_JOB_TYPE, "processing"),
            ]):
                session.add(ProcessingJobModel(
                    tenant_id=tenant, job_type=job_type,
                    entity_type="rrugc_candidate", entity_id=str(i),
                    idempotency_key="test-" + str(i), status=status,
                    updated_at=now,
                ))
            session.add_all([
                RrugcScoutQueryModel(
                    tenant_id="tenant-a", query="retro baseball cap",
                    query_normalized="retro baseball cap", lane="suggested",
                    claimed_by_agent_id="agent-a", lease_token="aa",
                    lease_expires_at=now + timedelta(minutes=4),
                    completed_cycles=3, failed_cycles=1,
                ),
                RrugcScoutQueryModel(
                    tenant_id="tenant-a", query="western trucker hat",
                    query_normalized="western trucker hat", lane="style",
                    completed_cycles=2,
                ),
                RrugcScoutQueryModel(
                    tenant_id="tenant-a", query="cowboy hat saying",
                    query_normalized="cowboy hat saying", lane="style",
                    claimed_by_agent_id="agent-other", lease_token="bb",
                    lease_expires_at=now + timedelta(minutes=3),
                ),
                RrugcScoutQueryModel(
                    tenant_id="tenant-b", query="private other tenant cap",
                    query_normalized="private other tenant cap", lane="product",
                    completed_cycles=500,
                ),
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-a", keyword="Retro Hat",
                    keyword_normalized="retro hat", created_at=now,
                ),
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-b", keyword="Private",
                    keyword_normalized="private", created_at=now,
                ),
                RrugcScoutFeedbackModel(
                    tenant_id="tenant-a", target_type="keyword",
                    target_key="retro hat", keyword="Retro Hat",
                    display_value="Retro Hat", status="suggested",
                    updated_by_user_id="owner",
                ),
                RrugcScoutMetricCycleModel(
                    tenant_id="tenant-a", agent_id="agent-a", mode="keyword",
                    machine_label="BaoNghia", cycle_id="job-1",
                    scanned_pins=32, new_keywords=5,
                ),
                RrugcScoutMetricCycleModel(
                    tenant_id="tenant-a", agent_id="agent-other", mode="keyword",
                    machine_label="Other", cycle_id="job-2",
                    scanned_pins=300, new_keywords=100,
                ),
            ])
            session.commit()
            result = scout_jobs_snapshot(session, "tenant-a", "agent-a", now=now)
            review = result["review"]
            keyword = result["keyword"]
            assert review["scout_active"] == 1
            assert review["scout_completed_24h"] == 1
            assert review["scout_failed_24h"] == 0
            assert review["stage1_pending"] == 2
            assert review["stage1_running"] == 1
            assert review["stage1_completed_24h"] == 1
            assert review["stage1_failed_24h"] == 1
            assert keyword["active_searches"] == 1
            assert keyword["tenant_active_searches"] == 2
            assert keyword["ready_queries"] == 1
            assert keyword["total_queries"] == 3
            assert keyword["completed_cycles_total"] == 5
            assert keyword["failed_cycles_total"] == 1
            assert keyword["suggestions_pending"] == 1
            assert keyword["new_keywords_24h"] == 1
            assert keyword["scanned_pins_24h_agent"] == 32
            assert keyword["saved_keywords_24h_agent"] == 5
    finally:
        engine.dispose()


def test_scout_jobs_snapshot_zero_is_real_zero():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        ProcessingJobModel.__table__,
        RrugcScoutRunModel.__table__,
        RrugcScoutQueryModel.__table__,
        RrugcScoutMetricCycleModel.__table__,
        RrugcKeywordVolumeModel.__table__,
        RrugcScoutFeedbackModel.__table__,
    ):
        table.create(engine)
    try:
        with Session(engine) as session:
            result = scout_jobs_snapshot(session, "empty-tenant", "unused-agent")
            assert result["review"]["stage1_pending"] == 0
            assert result["keyword"]["active_searches"] == 0
            assert result["keyword"]["total_queries"] == 0
            assert result["keyword"]["new_keywords_24h"] == 0
    finally:
        engine.dispose()
