"""Durable and tenant-scoped historical Gemini replay requests.

The queued replay writes to a fresh snapshot copy, never directly to the shared
operational workbook. Promotion only updates the verified Gemini source pointer.
Morning Reset recovery remains a separate preview/apply workflow.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily_sheet.service import InventoryDailySheetService, inventory_error_metadata
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.persistence_model import InventorySettingsModel

REPLAY_REQUEST_TYPE = "inventory_v4_historical_replay_request"
REPLAY_MAX_ATTEMPTS = 72
REPLAY_LEASE_SECONDS = 60 * 60
REPLAY_RETRY_MIN_SECONDS = 5 * 60
logger = logging.getLogger("cam.inventory.historical_replay_queue")


class HistoricalReplayQueue:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        service_factory: Callable[[], InventoryDailySheetService] | None = None,
        recovery: Callable[..., Any] | None = None,
    ):
        self.session_factory = session_factory
        self.service_factory = service_factory or (lambda: InventoryDailySheetService(
            session_factory,
            semantic_analyzer=build_daily_sheet_semantic_analyzer(session_factory=session_factory),
        ))
        # Intentionally do NOT auto-apply/reset shared Sheet when replay succeeds.
        self.recovery = recovery

    @staticmethod
    def _view(job: InventoryJobModel) -> dict[str, Any]:
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        return {
            "job_id": job.id,
            "business_date": job.entity_id,
            "status": job.status,
            "mode": payload.get("mode", "fresh_copy"),
            "promote": bool(payload.get("promote", True)),
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
            "last_error_code": job.last_error_code,
            "result": payload.get("result"),
        }

    def enqueue(self, tenant_id: str, business_date: date, mode: str = "fresh_copy", promote: bool = True) -> dict[str, Any]:
        if mode != "fresh_copy":
            # Retryable queue requests always start from an isolated immutable snapshot.
            raise ValueError("inventory_historical_replay_queue_fresh_copy_only")
        with self.session_factory.begin() as session:
            query = select(InventorySettingsModel).where(
                InventorySettingsModel.tenant_id == tenant_id,
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            settings = session.scalar(query)
            if (
                settings is None or not settings.enabled
                or not settings.daily_sheet_automation_enabled
                or not isinstance(settings.daily_sheet_config_json, dict)
                or settings.daily_sheet_config_json.get("version") != 4
            ):
                raise ValueError("inventory_historical_replay_queue_not_configured")
            active = session.scalar(select(InventoryJobModel).where(
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.job_type == REPLAY_REQUEST_TYPE,
                InventoryJobModel.entity_id == business_date.isoformat(),
                InventoryJobModel.status.in_(("pending", "retry", "processing")),
            ).order_by(InventoryJobModel.created_at.desc()).limit(1))
            if active is not None:
                if bool((active.payload_json or {}).get("promote", True)) != promote:
                    raise ValueError("inventory_historical_replay_conflicting_active_request")
                return self._view(active)
            job = InventoryJobRepository(session, (REPLAY_REQUEST_TYPE,)).create_job(
                tenant_id=tenant_id,
                job_type=REPLAY_REQUEST_TYPE,
                entity_type="inventory_daily_sheet_snapshot",
                entity_id=business_date.isoformat(),
                idempotency_key=f"inventory-replay-request:{tenant_id}:{business_date.isoformat()}:{uuid4()}",
                payload={"business_date": business_date.isoformat(), "mode": mode, "promote": promote},
                max_attempts=REPLAY_MAX_ATTEMPTS,
            )
            return self._view(job)

    def status(self, tenant_id: str, job_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            job = session.scalar(select(InventoryJobModel).where(
                InventoryJobModel.id == job_id,
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.job_type == REPLAY_REQUEST_TYPE,
            ))
            if job is None:
                raise LookupError("inventory_replay_job_not_found")
            return self._view(job)

    def active_jobs(self, tenant_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            jobs = session.scalars(select(InventoryJobModel).where(
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.job_type == REPLAY_REQUEST_TYPE,
                InventoryJobModel.status.in_(("pending", "retry", "processing")),
            ).order_by(InventoryJobModel.created_at.desc()).limit(limit))
            return [self._view(job) for job in jobs]

    def run_once(self, allowed_tenant_ids: frozenset[str] | None) -> int:
        """Process one durable request; V5J timer calls this once each tick."""
        if allowed_tenant_ids is not None and not allowed_tenant_ids:
            return 0
        now = datetime.now(timezone.utc)
        worker_id = f"inventory-replay-{uuid4()}"
        with self.session_factory.begin() as session:
            eligible = or_(
                and_(InventoryJobModel.status.in_(("pending", "retry")),
                     InventoryJobModel.next_attempt_at <= now),
                and_(InventoryJobModel.status == "processing",
                     InventoryJobModel.lease_expires_at <= now),
            )
            query = select(InventoryJobModel).join(
                InventorySettingsModel,
                InventorySettingsModel.tenant_id == InventoryJobModel.tenant_id,
            ).where(
                InventoryJobModel.job_type == REPLAY_REQUEST_TYPE,
                InventoryJobModel.cancellation_requested.is_(False),
                InventoryJobModel.attempt_count < InventoryJobModel.max_attempts,
                InventorySettingsModel.enabled.is_(True),
                InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
                eligible,
            ).order_by(InventoryJobModel.created_at).limit(1)
            if allowed_tenant_ids is not None:
                query = query.where(InventoryJobModel.tenant_id.in_(allowed_tenant_ids))
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True, of=InventoryJobModel)
            job = session.scalar(query)
            if job is None:
                return 0
            job.status = "processing"
            job.claimed_by = worker_id
            job.claimed_at = now
            job.lease_expires_at = now + timedelta(seconds=REPLAY_LEASE_SECONDS)
            job.attempt_count += 1
            job.updated_at = now
            job_id = job.id
            tenant_id = job.tenant_id
            day = date.fromisoformat(job.entity_id)
            payload = dict(job.payload_json or {})
            mode = str(payload.get("mode") or "fresh_copy")
            promote = bool(payload.get("promote", True))
        try:
            result = self.service_factory().replay_agent_v4_historical(
                tenant_id, day, mode=mode, promote=promote,
            )
            with self.session_factory.begin() as session:
                job = session.get(InventoryJobModel, job_id)
                if job is None or job.status != "processing" or job.claimed_by != worker_id:
                    raise RuntimeError("inventory_replay_job_lease_lost")
                job.payload_json = {**job.payload_json, "result": result}
                InventoryJobRepository(session, (REPLAY_REQUEST_TYPE,)).complete(job, worker_id)
            return 1
        except Exception as error:
            code = str(getattr(error, "code", str(error) or type(error).__name__))[:100]
            explicit = getattr(error, "retryable", None)
            retryable = explicit if isinstance(explicit, bool) else bool(inventory_error_metadata(code)["retryable"])
            if code == "inventory_historical_replay_in_progress":
                retryable = True
            # Generic provider HTTP 4xx must never revive as transport errors.
            if not retryable and code == "inventory_gemini_request_failed":
                code = "inventory_gemini_provider_rejected"
            with self.session_factory.begin() as session:
                job = session.get(InventoryJobModel, job_id)
                if job is not None and job.status == "processing" and job.claimed_by == worker_id:
                    can_retry = InventoryJobRepository(session, (REPLAY_REQUEST_TYPE,)).fail(
                        job, worker_id, error_code=code, error_message=code,
                        retryable=retryable,
                    )
                    if can_retry:
                        job.next_attempt_at = max(
                            job.next_attempt_at,
                            datetime.now(timezone.utc) + timedelta(seconds=REPLAY_RETRY_MIN_SECONDS),
                        )
            logger.warning("inventory_historical_replay_job_failed tenant_id=%s business_date=%s error_code=%s retryable=%s",
                           tenant_id, day.isoformat(), code, retryable)
            return 0
