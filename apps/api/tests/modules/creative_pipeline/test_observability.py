from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.modules.creative_pipeline.model import ArtifactModel, NodeRunModel, PipelineRunModel
from app.modules.creative_pipeline.observability import CreativePipelineObservabilityService
from app.modules.processing.model import ProcessingJobModel
from tests.modules.creative_pipeline.test_domain import _group, _listing, _session


def _seed(session, tenant="tenant-a", source="source-a"):
    group = _group(session, tenant=tenant, source=source, folder=f"group-{tenant}")
    listing = _listing(session, group, folder=f"listing-{tenant}", listing_key=f"key-{tenant}")
    run = PipelineRunModel(
        id=str(uuid4()), tenant_id=tenant, listing_task_id=listing.id,
        run_number=1, status="completed", trigger_type="discovery",
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    node = NodeRunModel(
        id=str(uuid4()), tenant_id=tenant, pipeline_run_id=run.id, node_type="input_data",
        status="failed", attempt_count=2, max_attempts=5, last_error_code="provider_timeout",
    )
    session.add(node)
    session.flush()
    session.add(ArtifactModel(
        id=str(uuid4()), tenant_id=tenant, listing_task_id=listing.id, pipeline_run_id=run.id,
        node_run_id=node.id, artifact_type="input_snapshot", version=1,
        relative_path="Pipeline/Input/input_snapshot_v001.json", status="available",
    ))
    session.add_all([
        ProcessingJobModel(
            id=str(uuid4()), tenant_id=tenant, job_type="creative_pipeline_node",
            entity_type="creative_pipeline_node_run", entity_id=node.id,
            idempotency_key=f"node-{tenant}", status="retry", attempt_count=2, max_attempts=5,
            processing_duration_ms=120, last_error_code="provider_timeout",
        ),
        ProcessingJobModel(
            id=str(uuid4()), tenant_id=tenant, job_type="creative_pipeline_scan",
            entity_type="creative_pipeline_canary_root", entity_id=f"root-{tenant}",
            idempotency_key=f"scan-{tenant}", status="completed", max_attempts=3,
            processing_duration_ms=40,
        ),
    ])
    return run, node


def test_observability_is_tenant_scoped_and_reports_durable_pipeline_state():
    engine, sessions = _session()
    try:
        ProcessingJobModel.__table__.create(engine, checkfirst=True)
        with sessions.begin() as session:
            _seed(session)
            _seed(session, tenant="tenant-b", source="source-b")

        with sessions() as session:
            snapshot = CreativePipelineObservabilityService(session).snapshot(
                "tenant-a", now=datetime.now(timezone.utc)
            )

        assert snapshot["tenant_id"] == "tenant-a"
        assert snapshot["runs"]["total"] == 1
        assert snapshot["runs"]["by_status"] == {"completed": 1}
        assert snapshot["runs"]["completed_last_24h"] == 1
        assert snapshot["nodes"]["by_status"] == {"failed": 1}
        assert snapshot["nodes"]["by_type_status"] == [
            {"node_type": "input_data", "status": "failed", "count": 1}
        ]
        assert snapshot["jobs"]["by_status"] == {"completed": 1, "retry": 1}
        assert snapshot["jobs"]["active_total"] == 1
        assert snapshot["jobs"]["retry_total"] == 1
        assert snapshot["jobs"]["scan_by_status"] == {"completed": 1}
        assert snapshot["jobs"]["processing_duration_ms"] == {
            "sample_count": 2, "p50": 40, "p95": 120, "max": 120,
        }
        assert snapshot["artifacts"] == {"total": 1, "by_status": {"available": 1}}
        assert {row["kind"] for row in snapshot["recent_failures"]} == {"node", "job"}
        assert all("last_error_message" not in row for row in snapshot["recent_failures"])
    finally:
        engine.dispose()


def test_observability_empty_tenant_is_zero_and_does_not_write():
    engine, sessions = _session()
    try:
        ProcessingJobModel.__table__.create(engine, checkfirst=True)
        before = datetime.now(timezone.utc)
        with sessions() as session:
            snapshot = CreativePipelineObservabilityService(session).snapshot("tenant-a", now=before)

        assert snapshot["runs"] == {
            "total": 0, "by_status": {}, "terminal_total": 0, "completed_last_24h": 0,
        }
        assert snapshot["nodes"] == {"total": 0, "by_status": {}, "by_type_status": []}
        assert snapshot["jobs"]["total"] == 0
        assert snapshot["jobs"]["processing_duration_ms"] == {
            "sample_count": 0, "p50": None, "p95": None, "max": None,
        }
        assert snapshot["artifacts"] == {"total": 0, "by_status": {}}
        assert snapshot["recent_failures"] == []
    finally:
        engine.dispose()


def test_observability_excludes_old_completion_from_recent_window():
    engine, sessions = _session()
    try:
        ProcessingJobModel.__table__.create(engine, checkfirst=True)
        with sessions.begin() as session:
            run, _ = _seed(session)
            run.completed_at = datetime.now(timezone.utc) - timedelta(hours=25)
        with sessions() as session:
            snapshot = CreativePipelineObservabilityService(session).snapshot("tenant-a")
        assert snapshot["runs"]["completed_last_24h"] == 0
    finally:
        engine.dispose()

