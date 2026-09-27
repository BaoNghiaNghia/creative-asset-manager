from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcReviewTaskModel,
    RrugcSupervisorResultModel,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.service import RrugcError


REVIEW_NOTE_MAX = 1000


@dataclass(frozen=True, slots=True)
class ReviewReconcileResult:
    scanned: int
    created: int


class RrugcReviewService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = RrugcRepository(session)

    def ensure_from_supervisor(
        self,
        *,
        result: RrugcSupervisorResultModel,
    ) -> tuple[RrugcReviewTaskModel | None, bool]:
        if result.status not in {"pass", "needs_human_review"}:
            return None, False

        existing = self.repository.review_task_for_attempt(
            result.tenant_id,
            result.generation_attempt_id,
        )
        if existing is not None:
            self._ensure_attempt_link(existing)
            return existing, False

        attempt = self.repository.lock_generation_attempt(
            result.tenant_id,
            result.generation_attempt_id,
        )
        if attempt is None or attempt.status != "completed" or not attempt.output_remote_file_id:
            raise RrugcError(
                "review_generation_output_not_ready",
                "A completed stored generation output is required for review handoff.",
                status_code=409,
            )

        human_review = result.status == "needs_human_review"
        row = RrugcReviewTaskModel(
            tenant_id=result.tenant_id,
            campaign_id=result.campaign_id,
            candidate_id=result.candidate_id,
            product_id=result.product_id,
            generation_attempt_id=result.generation_attempt_id,
            supervisor_result_id=result.id,
            queue_reason=(
                "supervisor_needs_human_review"
                if human_review
                else "supervisor_pass"
            ),
            priority="high" if human_review else "standard",
            status="pending",
        )
        try:
            self.session.add(row)
            self.session.flush()
            attempt.review_status = "pending"
            attempt.review_task_id = row.id
            attempt.reviewed_by_user_id = None
            attempt.reviewed_at = None
            attempt.review_note = None
            attempt.export_status = "pending_review"
            self.session.commit()
            self.session.refresh(row)
            return row, True
        except IntegrityError:
            self.session.rollback()
            existing = self.repository.review_task_for_attempt(
                result.tenant_id,
                result.generation_attempt_id,
            )
            if existing is None:
                raise
            self._ensure_attempt_link(existing)
            return existing, False

    def transition(
        self,
        *,
        tenant_id: str,
        task_id: str,
        target_status: str,
        user_id: str,
        review_note: str | None = None,
    ) -> tuple[RrugcReviewTaskModel, bool]:
        if target_status not in {"approved", "rejected"}:
            raise RrugcError(
                "review_transition_invalid",
                "Review transition is invalid.",
                status_code=422,
            )
        note = (review_note or "").strip() or None
        if note is not None and len(note) > REVIEW_NOTE_MAX:
            raise RrugcError(
                "review_note_too_long",
                f"Review note must be {REVIEW_NOTE_MAX} characters or fewer.",
                status_code=422,
            )

        task = self.repository.lock_review_task(tenant_id, task_id)
        if task is None:
            raise RrugcError(
                "review_task_not_found",
                "Review task not found.",
                status_code=404,
            )
        if task.status == target_status:
            return task, False
        if task.status in {"approved", "rejected"}:
            raise RrugcError(
                "review_transition_conflict",
                f"Review task is already {task.status}.",
                status_code=409,
            )
        if task.status != "pending":
            raise RrugcError(
                "review_transition_invalid_state",
                "Review task is not pending.",
                status_code=409,
            )

        attempt = self.repository.lock_generation_attempt(
            tenant_id,
            task.generation_attempt_id,
        )
        if attempt is None:
            raise RrugcError(
                "generation_attempt_not_found",
                "Generation attempt not found.",
                status_code=404,
            )
        reviewed_at = datetime.now(timezone.utc)
        task.status = target_status
        task.reviewed_by_user_id = user_id
        task.reviewed_at = reviewed_at
        task.review_note = note

        attempt.review_status = target_status
        attempt.review_task_id = task.id
        attempt.reviewed_by_user_id = user_id
        attempt.reviewed_at = reviewed_at
        attempt.review_note = note
        attempt.export_status = (
            "export_ready" if target_status == "approved" else "not_exportable"
        )
        self.session.commit()
        self.session.refresh(task)
        return task, True

    def reconcile(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
    ) -> ReviewReconcileResult:
        results = self.repository.terminal_supervisor_results_without_review(
            tenant_id,
            limit=limit,
        )
        created = 0
        for result in results:
            _, was_created = self.ensure_from_supervisor(result=result)
            if was_created:
                created += 1
        return ReviewReconcileResult(scanned=len(results), created=created)

    def _ensure_attempt_link(self, task: RrugcReviewTaskModel) -> None:
        attempt = self.repository.lock_generation_attempt(
            task.tenant_id,
            task.generation_attempt_id,
        )
        if attempt is None:
            return
        changed = False
        if attempt.review_task_id != task.id:
            attempt.review_task_id = task.id
            changed = True
        if task.status == "pending":
            if attempt.review_status != "pending":
                attempt.review_status = "pending"
                changed = True
            if attempt.export_status != "pending_review":
                attempt.export_status = "pending_review"
                changed = True
        elif task.status in {"approved", "rejected"}:
            expected_export = (
                "export_ready" if task.status == "approved" else "not_exportable"
            )
            if attempt.review_status != task.status:
                attempt.review_status = task.status
                changed = True
            if attempt.export_status != expected_export:
                attempt.export_status = expected_export
                changed = True
            if attempt.reviewed_by_user_id != task.reviewed_by_user_id:
                attempt.reviewed_by_user_id = task.reviewed_by_user_id
                changed = True
            if attempt.reviewed_at != task.reviewed_at:
                attempt.reviewed_at = task.reviewed_at
                changed = True
            if attempt.review_note != task.review_note:
                attempt.review_note = task.review_note
                changed = True
        if changed:
            self.session.commit()
