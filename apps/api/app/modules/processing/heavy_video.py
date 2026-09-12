from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.processing.model import (
    PROCESSING_JOB_TERMINAL_STATUSES,
    ProcessingJobModel,
    ProcessingResourceLeaseModel,
)

HEAVY_VIDEO_RESOURCE_KEY = "heavy_video"
VIDEO_ANALYSIS_OWNER_TYPE = "video_analysis"
VIDEO_GENERATION_OWNER_TYPE = "video_generation"


class HeavyVideoResource:
    """Capacity-one global lane shared by video analysis and Dola generation.

    The migration creates a single slot. Acquiring it locks the stable row instead
    of relying on process-local state, so a worker restart or another tenant cannot
    bypass ownership. Generation leases deliberately have no time based expiry;
    Dola polling and submission-unknown recovery keep their owner until the run is
    authoritatively terminal. Analysis is the only recoverable stale owner because
    its ProcessingJob lease is authoritative for active execution.
    """

    def __init__(self, session: Session):
        self.session = session

    def acquire(self, *, owner_type: str, owner_id: str) -> bool:
        self._recover_stale_analysis_owner()
        slot = self.session.scalar(
            select(ProcessingResourceLeaseModel)
            .where(ProcessingResourceLeaseModel.resource_key == HEAVY_VIDEO_RESOURCE_KEY)
            .with_for_update()
        )
        if slot is None:
            # Defensive support for metadata-created development/test databases.
            # Production always has this seed row from Alembic 0068.
            slot = ProcessingResourceLeaseModel(resource_key=HEAVY_VIDEO_RESOURCE_KEY)
            self.session.add(slot)
            self.session.flush()
        if slot.owner_type is not None:
            return slot.owner_type == owner_type and slot.owner_id == owner_id
        now = datetime.now(timezone.utc)
        slot.owner_type = owner_type
        slot.owner_id = owner_id
        slot.acquired_at = now
        slot.updated_at = now
        self.session.flush()
        return True

    def release(self, *, owner_type: str, owner_id: str) -> bool:
        slot = self.session.scalar(
            select(ProcessingResourceLeaseModel)
            .where(ProcessingResourceLeaseModel.resource_key == HEAVY_VIDEO_RESOURCE_KEY)
            .with_for_update()
        )
        if slot is None or slot.owner_type != owner_type or slot.owner_id != owner_id:
            return False
        slot.owner_type = None
        slot.owner_id = None
        slot.acquired_at = None
        slot.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return True

    def owner(self) -> tuple[str | None, str | None]:
        slot = self.session.get(ProcessingResourceLeaseModel, HEAVY_VIDEO_RESOURCE_KEY)
        if slot is None:
            return None, None
        return slot.owner_type, slot.owner_id

    def _recover_stale_analysis_owner(self) -> None:
        """Clear only analysis ownership whose authoritative job is terminal/stale.

        Never apply this recovery to a generation run: provider submission and poll
        recovery must remain serialized until the run itself reaches a terminal
        state, including ``submission_unknown``.
        """
        slot = self.session.scalar(
            select(ProcessingResourceLeaseModel)
            .where(ProcessingResourceLeaseModel.resource_key == HEAVY_VIDEO_RESOURCE_KEY)
            .with_for_update()
        )
        if slot is None or slot.owner_type != VIDEO_ANALYSIS_OWNER_TYPE or not slot.owner_id:
            return
        job = self.session.get(ProcessingJobModel, slot.owner_id)
        now = datetime.now(timezone.utc)
        lease_expires_at = job.lease_expires_at if job is not None else None
        if lease_expires_at is not None and (lease_expires_at.tzinfo is None or lease_expires_at.utcoffset() is None):
            lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
        expired = bool(lease_expires_at and lease_expires_at <= now)
        if job is None or job.status in PROCESSING_JOB_TERMINAL_STATUSES or expired:
            slot.owner_type = None
            slot.owner_id = None
            slot.acquired_at = None
            slot.updated_at = now
            self.session.flush()
