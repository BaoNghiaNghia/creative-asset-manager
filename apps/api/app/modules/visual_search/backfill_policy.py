from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.processing.model import ProcessingJobModel
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION


VISUAL_BACKFILL_ARCHIVE_PRIORITY = 5
VISUAL_BACKFILL_RECENT_PRIORITY = 15


@dataclass(frozen=True, slots=True)
class VisualBackfillPolicy:
    max_queued_jobs: int
    max_slice_assets: int
    recent_days: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "VisualBackfillPolicy":
        max_queued_jobs = max(
            1,
            min(
                int(getattr(settings, "VISUAL_SEARCH_BACKFILL_MAX_QUEUED_JOBS", 250)),
                5_000,
            ),
        )
        max_slice_assets = max(
            1,
            min(
                int(getattr(settings, "VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS", 100)),
                1_000,
            ),
        )
        recent_days = max(
            1,
            min(
                int(getattr(settings, "VISUAL_SEARCH_BACKFILL_RECENT_DAYS", 30)),
                3650,
            ),
        )
        return cls(
            max_queued_jobs=max_queued_jobs,
            max_slice_assets=max_slice_assets,
            recent_days=recent_days,
        )

    def priority_for_activity(
        self,
        activity_at: datetime | None,
        *,
        now: datetime | None = None,
    ) -> int:
        if activity_at is None:
            return VISUAL_BACKFILL_ARCHIVE_PRIORITY
        current = now or datetime.now(timezone.utc)
        if activity_at.tzinfo is None:
            activity_at = activity_at.replace(tzinfo=timezone.utc)
        if activity_at >= current - timedelta(days=self.recent_days):
            return VISUAL_BACKFILL_RECENT_PRIORITY
        return VISUAL_BACKFILL_ARCHIVE_PRIORITY


def active_visual_queue_depth(
    session: Session,
    *,
    tenant_id: str,
    schema_version: str = VISUAL_EMBEDDING_SCHEMA_VERSION,
) -> int:
    count = session.scalar(
        select(func.count(ProcessingJobModel.id)).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == "visual_index_sync",
            ProcessingJobModel.status.in_(("pending", "processing", "retry")),
            ProcessingJobModel.payload_json["embedding_schema_version"].as_string()
            == schema_version,
        )
    )
    return int(count or 0)