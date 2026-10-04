from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.visual_search.failed_job_recovery import recover_failed_visual_jobs
from app.modules.visual_search.lifecycle import (
    VISUAL_EMBEDDING_SCHEMA_VERSION,
    visual_index_job_key,
)


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed(session: Session, *, source_status: str = "active", oauth: str | None = "conn"):
    content_hash = "a" * 64
    external = ExternalSourceModel(
        id="external",
        tenant_id="tenant",
        source_key="drive",
        source_type="google_drive",
        status=source_status,
        oauth_connection_id=oauth,
    )
    source = SourceAssetModel(
        id="source",
        tenant_id="tenant",
        external_source_id="external",
        external_asset_id="file",
        filename="image.jpg",
        mime_type="image/jpeg",
    )
    asset = AssetModel(id="asset", tenant_id="tenant", content_hash=content_hash)
    link = AssetSourceLinkModel(
        id="link",
        tenant_id="tenant",
        asset_id="asset",
        source_asset_id="source",
    )
    job = ProcessingJobModel(
        id="job",
        tenant_id="tenant",
        job_type="visual_index_sync",
        entity_type="asset",
        entity_id="asset",
        idempotency_key=visual_index_job_key("asset", content_hash),
        payload_json={
            "asset_id": "asset",
            "source_asset_id": "source",
            "content_sha256": content_hash,
            "embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION,
        },
        status="failed",
        attempt_count=1,
        max_attempts=5,
        last_error_code="visual_index_source_unavailable",
        last_error_message="Source unavailable.",
    )
    session.add_all((external, source, asset, link, job))
    session.commit()
    return external, job


def test_failed_visual_job_is_recovered_once_while_source_is_streamable():
    engine = _database()
    try:
        with Session(engine) as session:
            _external, job = _seed(session)
            now = datetime.now(timezone.utc)
            assert recover_failed_visual_jobs(
                session, tenant_id="tenant", limit=10, now=now
            ) == 1
            assert job.status == "retry"
            assert job.last_error_code == "visual_index_recovery_requested"
            assert job.payload_json["_visual_recovery_requested_at"] == now.isoformat()

            job.status = "failed"
            job.last_error_code = "visual_index_source_unavailable"
            session.flush()
            assert recover_failed_visual_jobs(
                session,
                tenant_id="tenant",
                limit=10,
                now=now + timedelta(minutes=5),
            ) == 0
    finally:
        engine.dispose()


def test_source_reconnect_allows_another_recovery_after_previous_attempt():
    engine = _database()
    try:
        with Session(engine) as session:
            external, job = _seed(session)
            first = datetime.now(timezone.utc)
            assert recover_failed_visual_jobs(
                session, tenant_id="tenant", limit=10, now=first
            ) == 1
            job.status = "failed"
            job.last_error_code = "visual_index_source_unavailable"
            external.updated_at = first + timedelta(minutes=1)
            session.flush()
            second = first + timedelta(minutes=2)
            assert recover_failed_visual_jobs(
                session, tenant_id="tenant", limit=10, now=second
            ) == 1
            assert job.payload_json["_visual_recovery_requested_at"] == second.isoformat()
    finally:
        engine.dispose()


def test_failed_visual_job_waits_while_source_requires_reconnect():
    engine = _database()
    try:
        with Session(engine) as session:
            _external, job = _seed(
                session, source_status="reconnect_required", oauth="old"
            )
            assert recover_failed_visual_jobs(
                session, tenant_id="tenant", limit=10
            ) == 0
            assert job.status == "failed"
            assert job.last_error_code == "visual_index_source_unavailable"
    finally:
        engine.dispose()
