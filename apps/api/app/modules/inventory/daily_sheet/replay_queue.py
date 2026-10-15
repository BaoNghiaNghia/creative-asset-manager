"""Durable and tenant-scoped historical Gemini replay requests.

The queued replay writes to a fresh snapshot copy, never directly to the shared
operational workbook. Promotion only updates the verified Gemini source pointer.
Morning Reset recovery remains a separate preview/apply workflow.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Callable, Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily_sheet.service import InventoryDailySheetService, inventory_error_metadata
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.persistence_model import InventorySettingsModel, InventoryDailySheetSnapshotModel

REPLAY_REQUEST_TYPE = "inventory_v4_historical_replay_request"
REPLAY_MAX_ATTEMPTS = 72
REPLAY_LEASE_SECONDS = 60 * 60
REPLAY_RETRY_MIN_SECONDS = 5 * 60
AUTO_REPLAY_LOOKBACK_DAYS = 7
AUTO_REPLAY_POLICY_VERSION = 2
AUTO_RETRYABLE_ERRORS = frozenset({
    "inventory_gemini_rate_limited",
    "inventory_gemini_transport_error",
    "inventory_gemini_request_failed",
    "inventory_gemini_request_failed_429",
    "inventory_gemini_request_failed_500",
    "inventory_gemini_request_failed_502",
    "inventory_gemini_request_failed_503",
    "inventory_gemini_request_failed_504",
})
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
        self.last_auto_scan: dict[str, int] = {}

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

    def enqueue(self, tenant_id: str, business_date: date, mode: str = "fresh_copy", promote: bool = True, *, automatic: bool = False) -> dict[str, Any]:
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
                idempotency_key=(
                    f"inventory-replay-auto-v{AUTO_REPLAY_POLICY_VERSION}:{tenant_id}:{business_date.isoformat()}"
                    if automatic else
                    f"inventory-replay-request:{tenant_id}:{business_date.isoformat()}:{uuid4()}"
                ),
                payload={"business_date": business_date.isoformat(), "mode": mode, "promote": promote,
                         "automatic": automatic,
                         "automatic_policy_version": AUTO_REPLAY_POLICY_VERSION if automatic else None},
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

    @staticmethod
    def _allow_policy_upgrade(
        session: Session, tenant_id: str, day: date,
        prior_jobs: list[InventoryJobModel],
    ) -> bool:
        """At most one v2 replay for v1 model protocol failures.

        Never supersede a human job, active run, previously promoted result,
        permanent Google/auth failure, or a prior v2 attempt.
        """
        if not prior_jobs:
            return True
        if any(
            item.status in {"pending", "processing", "retry"}
            or (item.payload_json or {}).get("automatic") is not True
            or (item.payload_json or {}).get("automatic_policy_version", 1) >= AUTO_REPLAY_POLICY_VERSION
            for item in prior_jobs
        ):
            return False
        latest = prior_jobs[0]
        payload = latest.payload_json or {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if latest.status == "completed":
            return (
                result.get("status") == "review_required"
                and not bool(result.get("promoted"))
            )
        if latest.status != "failed" or latest.last_error_code != "inventory_sheet_agent_v4_safety_error":
            return False
        error = session.scalar(select(InventoryJobModel.last_error_message).where(
            InventoryJobModel.tenant_id == tenant_id,
            InventoryJobModel.job_type == "inventory_v4_historical_replay",
            InventoryJobModel.entity_id == day.isoformat(),
            InventoryJobModel.status == "failed",
        ).order_by(InventoryJobModel.created_at.desc()).limit(1))
        return str(error or "") == "unknown_tool"

    def enqueue_failed_evening_days(
        self, allowed_tenant_ids: frozenset[str] | None,
        *, now: datetime | None = None,
    ) -> int:
        """Rescue only recent, durable and transient historical Gemini failures.

        The active V5J lifecycle owns the preceding night's Gemini file until
        the configured Morning Reset boundary. A replay always starts from an
        immutable date snapshot; it never writes to the Shared Sheet. Permanent
        failures are deliberately excluded and one auto request per day is
        allowed, even after a terminal queue failure.
        """
        self.last_auto_scan = {
            "v4_tenants": 0, "snapshots": 0,
            "before_safe_boundary": 0, "snapshot_not_ready": 0,
            "verified": 0, "slot_not_retryable": 0,
            "already_requested": 0, "eligible": 0,
        }
        if allowed_tenant_ids is not None and not allowed_tenant_ids:
            return 0
        moment = now or datetime.now(timezone.utc)
        candidates: list[tuple[str, date]] = []
        with self.session_factory() as session:
            settings_rows = session.scalars(select(InventorySettingsModel).where(
                InventorySettingsModel.enabled.is_(True),
                InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
            )).all()
            for settings in settings_rows:
                if allowed_tenant_ids is not None and settings.tenant_id not in allowed_tenant_ids:
                    continue
                if (settings.daily_sheet_config_json or {}).get("version") != 4:
                    continue
                self.last_auto_scan["v4_tenants"] += 1
                try:
                    local = moment.astimezone(ZoneInfo(settings.timezone or "Asia/Ho_Chi_Minh"))
                except (KeyError, ValueError):
                    local = moment.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
                local_today = local.date()
                earliest = local_today - timedelta(days=AUTO_REPLAY_LOOKBACK_DAYS)
                try:
                    reset_clock = time.fromisoformat(settings.daily_carry_forward_time_local or "05:00")
                except ValueError:
                    reset_clock = time(5, 0)
                snapshots = session.scalars(
                    select(InventoryDailySheetSnapshotModel).where(
                        InventoryDailySheetSnapshotModel.tenant_id == settings.tenant_id,
                        InventoryDailySheetSnapshotModel.business_date >= earliest,
                        InventoryDailySheetSnapshotModel.business_date < local_today,
                    ).order_by(InventoryDailySheetSnapshotModel.business_date)
                ).all()
                for snapshot in snapshots:
                    self.last_auto_scan["snapshots"] += 1
                    day = snapshot.business_date
                    if snapshot.status != "completed" or not snapshot.snapshot_file_id:
                        self.last_auto_scan["snapshot_not_ready"] += 1
                        continue
                    if (
                        day == local_today - timedelta(days=1)
                        and local.time() < reset_clock
                    ):
                        self.last_auto_scan["before_safe_boundary"] += 1
                        continue
                    if snapshot.gemini_reconcile_status == "completed" and snapshot.gemini_reconcile_verified_at is not None:
                        self.last_auto_scan["verified"] += 1
                        continue
                    if snapshot.gemini_reconcile_status not in {"pending", "failed", "retryable_failure"}:
                        continue
                    if snapshot.gemini_reconcile_verified_at is not None:
                        continue
                    slot = session.scalar(select(InventoryJobModel).where(
                        InventoryJobModel.tenant_id == settings.tenant_id,
                        InventoryJobModel.job_type == "inventory_v5_evening_reconcile_slot",
                        InventoryJobModel.entity_id == f"{day.isoformat()}:evening_reconcile",
                    ))
                    if (
                        slot is None or slot.status not in {"failed", "retry"}
                        or str(slot.last_error_code or "").lower() not in AUTO_RETRYABLE_ERRORS
                    ):
                        self.last_auto_scan["slot_not_retryable"] += 1
                        continue
                    prior_jobs = session.scalars(select(InventoryJobModel).where(
                        InventoryJobModel.tenant_id == settings.tenant_id,
                        InventoryJobModel.job_type == REPLAY_REQUEST_TYPE,
                        InventoryJobModel.entity_id == day.isoformat(),
                    ).order_by(InventoryJobModel.created_at.desc())).all()
                    if not self._allow_policy_upgrade(
                        session, settings.tenant_id, day, prior_jobs,
                    ):
                        self.last_auto_scan["already_requested"] += 1
                        continue
                    candidates.append((settings.tenant_id, day))
                    self.last_auto_scan["eligible"] += 1
        enqueued = 0
        for tenant_id, day in candidates:
            try:
                result = self.enqueue(tenant_id, day, automatic=True)
                if result["status"] == "pending":
                    enqueued += 1
                    logger.warning(
                        "inventory_historical_replay_auto_queued tenant_id=%s business_date=%s job_id=%s",
                        tenant_id, day, result["job_id"],
                    )
            except ValueError as error:
                logger.warning(
                    "inventory_historical_replay_auto_skipped tenant_id=%s business_date=%s error=%s",
                    tenant_id, day, str(error),
                )
        return enqueued

    def recent_failure_reasons(
        self, allowed_tenant_ids: frozenset[str] | None,
        *, now: datetime | None = None,
    ) -> list[dict[str, str]]:
        """Safe read-only diagnostics from original Gemini replay audit jobs.

        Only stable machine-like error tokens appear in operations logs; Google
        Sheet contents, credentials, IDs and free-form provider responses do not.
        """
        if allowed_tenant_ids is not None and not allowed_tenant_ids:
            return []
        since = (now or datetime.now(timezone.utc)) - timedelta(days=AUTO_REPLAY_LOOKBACK_DAYS)
        with self.session_factory() as session:
            query = select(InventoryJobModel).where(
                InventoryJobModel.job_type == "inventory_v4_historical_replay",
                InventoryJobModel.status == "failed",
                InventoryJobModel.created_at >= since,
            ).order_by(InventoryJobModel.created_at.desc(), InventoryJobModel.entity_id.desc()).limit(30)
            if allowed_tenant_ids is not None:
                query = query.where(InventoryJobModel.tenant_id.in_(allowed_tenant_ids))
            result: list[dict[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for job in session.scalars(query):
                key = (job.tenant_id, job.entity_id)
                if key in seen:
                    continue
                seen.add(key)
                reason = str(job.last_error_message or "").strip()
                safe_reason = (
                    reason if re.fullmatch(r"[a-z][a-z0-9_]{2,99}", reason)
                    else "unstructured_error"
                )
                result.append({
                    "business_date": job.entity_id,
                    "error_code": str(job.last_error_code or "unknown")[:100],
                    "reason": safe_reason,
                })
            return result

    def recent_replay_results(
        self, allowed_tenant_ids: frozenset[str] | None,
        *, now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Summarize durable queue outcomes without workbook content."""
        if allowed_tenant_ids is not None and not allowed_tenant_ids:
            return []
        since = (now or datetime.now(timezone.utc)) - timedelta(days=AUTO_REPLAY_LOOKBACK_DAYS)
        with self.session_factory() as session:
            query = select(InventoryJobModel).where(
                InventoryJobModel.job_type == REPLAY_REQUEST_TYPE,
                InventoryJobModel.created_at >= since,
            ).order_by(InventoryJobModel.created_at.desc()).limit(20)
            if allowed_tenant_ids is not None:
                query = query.where(InventoryJobModel.tenant_id.in_(allowed_tenant_ids))
            results = []
            for job in session.scalars(query):
                payload = job.payload_json if isinstance(job.payload_json, dict) else {}
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                results.append({
                    "business_date": job.entity_id,
                    "status": job.status,
                    "attempt_count": job.attempt_count,
                    "result_status": str(result.get("status") or "none")[:35],
                    "verification_status": str(result.get("verification_status") or "none")[:35],
                    "promoted": bool(result.get("promoted")),
                    "error_code": str(job.last_error_code or "none")[:100],
                })
            return results

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
            # DailySheetConfigurationError uses a generic class-level .code;
            # retain specific stable inventory_* messages for retry routing.
            details = str(error).strip()
            if code == "invalid_configuration" and details.startswith("inventory_"):
                code = details[:100]
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
