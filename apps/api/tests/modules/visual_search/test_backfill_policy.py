from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.visual_search.backfill_policy import (
    VISUAL_BACKFILL_ARCHIVE_PRIORITY,
    VISUAL_BACKFILL_RECENT_PRIORITY,
    VisualBackfillPolicy,
    active_visual_queue_depth,
)
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION


def test_progressive_policy_prioritizes_recent_assets_below_live_writes() -> None:
    policy = VisualBackfillPolicy.from_settings(
        Settings(
            VISUAL_SEARCH_BACKFILL_MAX_QUEUED_JOBS=250,
            VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS=100,
            VISUAL_SEARCH_BACKFILL_RECENT_DAYS=30,
        )
    )
    now = datetime.now(timezone.utc)

    assert policy.priority_for_activity(now - timedelta(days=2), now=now) == VISUAL_BACKFILL_RECENT_PRIORITY
    assert policy.priority_for_activity(now - timedelta(days=90), now=now) == VISUAL_BACKFILL_ARCHIVE_PRIORITY
    assert policy.priority_for_activity(None, now=now) == VISUAL_BACKFILL_ARCHIVE_PRIORITY
    assert VISUAL_BACKFILL_RECENT_PRIORITY < 20


def test_active_visual_queue_depth_is_tenant_schema_and_status_scoped() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        common = {
            "job_type": "visual_index_sync",
            "entity_type": "asset",
            "provider_key": "visual_encoder",
            "provider_scope": "visual",
        }
        session.add_all(
            [
                ProcessingJobModel(
                    tenant_id="tenant-a",
                    entity_id="a",
                    idempotency_key="a",
                    payload_json={"embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION},
                    status="pending",
                    **common,
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a",
                    entity_id="b",
                    idempotency_key="b",
                    payload_json={"embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION},
                    status="retry",
                    **common,
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a",
                    entity_id="c",
                    idempotency_key="c",
                    payload_json={"embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION},
                    status="completed",
                    **common,
                ),
                ProcessingJobModel(
                    tenant_id="tenant-b",
                    entity_id="d",
                    idempotency_key="d",
                    payload_json={"embedding_schema_version": VISUAL_EMBEDDING_SCHEMA_VERSION},
                    status="pending",
                    **common,
                ),
            ]
        )
        session.flush()

        assert active_visual_queue_depth(session, tenant_id="tenant-a") == 2
    finally:
        session.close()
        engine.dispose()
