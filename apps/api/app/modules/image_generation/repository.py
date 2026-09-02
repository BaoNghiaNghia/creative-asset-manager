from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.image_generation.model import ImageGenerationRunModel

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
ALLOWED_TRANSITIONS = {
    "queued": {"preparing", "cancelled", "failed"},
    "preparing": {"submitted", "running", "storing", "cancelled", "failed"},
    "submitted": {"running", "cancelled", "failed"},
    "running": {"running", "storing", "cancelled", "failed"},
    "storing": {"storing", "completed", "cancelled", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class ImageGenerationStateError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImageGenerationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, tenant_id: str, generation_id: str) -> ImageGenerationRunModel | None:
        return self.session.scalar(
            select(ImageGenerationRunModel).where(
                ImageGenerationRunModel.tenant_id == tenant_id,
                ImageGenerationRunModel.id == generation_id,
            )
        )

    def get_for_update(self, tenant_id: str, generation_id: str) -> ImageGenerationRunModel | None:
        return self.session.scalar(
            select(ImageGenerationRunModel)
            .where(
                ImageGenerationRunModel.tenant_id == tenant_id,
                ImageGenerationRunModel.id == generation_id,
            )
            .with_for_update()
        )

    def get_by_client_request(
        self, tenant_id: str, user_id: str, client_request_id: str
    ) -> ImageGenerationRunModel | None:
        return self.session.scalar(
            select(ImageGenerationRunModel).where(
                ImageGenerationRunModel.tenant_id == tenant_id,
                ImageGenerationRunModel.created_by_user_id == user_id,
                ImageGenerationRunModel.client_request_id == client_request_id,
            )
        )

    def create_idempotent(self, **values: object) -> tuple[ImageGenerationRunModel, bool]:
        existing = self.get_by_client_request(
            str(values["tenant_id"]),
            str(values["created_by_user_id"]),
            str(values["client_request_id"]),
        )
        if existing is not None:
            return existing, False
        try:
            with self.session.begin_nested():
                row = ImageGenerationRunModel(**values)
                self.session.add(row)
                self.session.flush()
            return row, True
        except IntegrityError:
            existing = self.get_by_client_request(
                str(values["tenant_id"]),
                str(values["created_by_user_id"]),
                str(values["client_request_id"]),
            )
            if existing is None:
                raise
            return existing, False

    def transition(self, run: ImageGenerationRunModel, status: str) -> None:
        if status == run.status:
            return
        if status not in ALLOWED_TRANSITIONS.get(run.status, set()):
            raise ImageGenerationStateError(f"{run.status}->{status}")
        run.status = status
        run.updated_at = utcnow()
        if status == "submitted" and run.submitted_at is None:
            run.submitted_at = run.updated_at
        if status in TERMINAL_STATUSES:
            run.completed_at = run.updated_at
        self.session.flush()

    def fail(self, run: ImageGenerationRunModel, code: str, message: str) -> None:
        if run.status in TERMINAL_STATUSES:
            return
        run.last_error_code = code[:100]
        run.last_error_message = message[:1000]
        self.transition(run, "failed")

    def cancel(self, run: ImageGenerationRunModel) -> None:
        if run.status in {"completed", "failed"}:
            raise ImageGenerationStateError(f"{run.status}->cancelled")
        if run.status != "cancelled":
            self.transition(run, "cancelled")
