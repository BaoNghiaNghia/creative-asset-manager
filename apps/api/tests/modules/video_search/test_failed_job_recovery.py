from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.video_search.failed_job_recovery import recover_video_failures


def seed(session, *, error="gemini_video_transport_error", active=True):
    external = ExternalSourceModel(
        id="external", tenant_id="t", source_key="one",
        source_type="onedrive", status="active" if active else "reconnect_required",
        oauth_connection_id="connection",
    )
    source = SourceAssetModel(
        id="source", tenant_id="t", external_source_id="external",
        external_asset_id="remote", filename="video.mp4", mime_type="video/mp4",
    )
    job = ProcessingJobModel(
        tenant_id="t", job_type="video_analyze",
        entity_type="source_asset", entity_id=source.id,
        idempotency_key="video-analyze-test", payload_json={"source_asset_id": "source"},
        status="failed", last_error_code=error, attempt_count=5, max_attempts=5,
    )
    session.add_all([external, source, job])
    session.commit()
    return job


def test_video_transient_retry_requires_live_source_and_only_happens_once():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        job = seed(session)
        assert recover_video_failures(session, "other", limit=10, apply=True)["recovered"] == 0
        assert recover_video_failures(session, "t", limit=10)["eligible"] == 1
        assert job.status == "failed"
        assert recover_video_failures(session, "t", limit=10, apply=True)["recovered"] == 1
        assert job.status == "retry"
        assert job.max_attempts == 6
        assert job.payload_json.get("_video_recovery_requested_at")
        job.status = "failed"
        job.last_error_code = "gemini_video_transport_error"
        assert recover_video_failures(session, "t", limit=10, apply=True)["already_retried"] == 1
        assert recover_video_failures(session, "t", limit=10, apply=True)["recovered"] == 0
    engine.dispose()


def test_video_disk_failure_never_retries_without_space_or_live_source():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        job = seed(session, error="video_proxy_storage_exhausted")
        assert recover_video_failures(session, "t", apply=True)["needs_disk"] == 1
        assert job.status == "failed"
        session.get(ExternalSourceModel, "external").status = "reconnect_required"
        assert recover_video_failures(session, "t", storage_ready=True, apply=True)["inaccessible"] == 1
        assert job.status == "failed"
    engine.dispose()


def test_non_retryable_gemini_bad_request_never_queued():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        job = seed(session, error="gemini_video_bad_request")
        assert recover_video_failures(session, "t", apply=True)["eligible"] == 0
        assert job.status == "failed"
    engine.dispose()
