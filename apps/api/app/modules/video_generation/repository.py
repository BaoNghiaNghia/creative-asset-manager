from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .model import VideoGenerationRunModel


class VideoGenerationStateError(ValueError):
    pass


_TRANSITIONS = {
    "queued": {"preparing", "cancelled"},
    "preparing": {"submitted", "submission_unknown", "failed", "cancelled"},
    "submitted": {"running", "submission_unknown", "failed", "cancelled"},
    "running": {"storing", "failed", "cancelled"},
    "submission_unknown": {"submitted", "running", "failed", "cancelled"},
    "storing": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class VideoGenerationRepository:
    """Persistence boundary; methods flush but never commit."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, tenant_id: str, generation_id: str) -> VideoGenerationRunModel | None:
        return self.session.scalar(
            select(VideoGenerationRunModel).where(
                VideoGenerationRunModel.tenant_id == tenant_id,
                VideoGenerationRunModel.id == generation_id,
            )
        )

    def get_by_client_request(
        self, tenant_id: str, user_id: str, client_request_id: str
    ) -> VideoGenerationRunModel | None:
        return self.session.scalar(
            select(VideoGenerationRunModel).where(
                VideoGenerationRunModel.tenant_id == tenant_id,
                VideoGenerationRunModel.created_by_user_id == user_id,
                VideoGenerationRunModel.client_request_id == client_request_id,
            )
        )

    def create_idempotent(self, run: VideoGenerationRunModel) -> tuple[VideoGenerationRunModel, bool]:
        existing = self.get_by_client_request(
            run.tenant_id, run.created_by_user_id, run.client_request_id
        )
        if existing is not None:
            return existing, False
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
            return run, True
        except IntegrityError:
            existing = self.get_by_client_request(
                run.tenant_id, run.created_by_user_id, run.client_request_id
            )
            if existing is None:
                raise
            return existing, False

    def transition(self, run: VideoGenerationRunModel, target: str) -> VideoGenerationRunModel:
        if target not in _TRANSITIONS.get(run.status, set()):
            raise VideoGenerationStateError(f"{run.status} -> {target} is not allowed")
        run.status = target
        now = datetime.now(timezone.utc)
        if target == "submitted" and run.submitted_at is None:
            run.submitted_at = now
        if target == "completed" and run.completed_at is None:
            run.completed_at = now
        self.session.flush()
        return run
