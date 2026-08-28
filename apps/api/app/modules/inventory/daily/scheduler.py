from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker
from app.modules.inventory.daily.service import InventoryDailyRunService
from app.modules.inventory.daily.report import DailyReportNotFinalized, InventoryDailyReportService
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.persistence_model import InventorySettingsModel

logger = logging.getLogger(__name__)

V4_SLOT_KINDS = frozenset({"snapshot", "reconcile"})
V4_SLOT_JOB_TYPES = {
    "snapshot": "inventory_v41_snapshot_slot",
    "reconcile": "inventory_v41_reconcile_slot",
}
V4_SETTLED_RESULTS = frozenset({"completed", "shadow", "review_required"})
V4_SLOT_MAX_ATTEMPTS = 5
V4_SLOT_LEASE_SECONDS = 15 * 60


def _configured_time(value: str, fallback: time) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour, minute)
    except (AttributeError, TypeError, ValueError):
        return fallback


class InventoryDailyScheduler:
    """Persisted, tenant-aware scheduler for legacy image and daily-sheet workflows."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        service: InventoryDailyRunService | None = None,
        report_service: InventoryDailyReportService | None = None,
        sheet_service: InventoryDailySheetService | None = None,
        *,
        allowed_tenant_ids: frozenset[str] | None = None,
    ):
        self.session_factory = session_factory
        self.service = service or InventoryDailyRunService(session_factory)
        self.report_service = report_service or InventoryDailyReportService(session_factory)
        if sheet_service is not None:
            self.sheet_service = sheet_service
        else:
            semantic_analyzer = build_daily_sheet_semantic_analyzer(
                session_factory=session_factory
            )
            self.sheet_service = InventoryDailySheetService(
                session_factory, semantic_analyzer=semantic_analyzer
            )
        self.allowed_tenant_ids = allowed_tenant_ids
        # V1/V2 use persisted daily records. V3 has no persistence migration in
        # its first release, so suppress repeated successful planning within the
        # long-lived scheduler process while still retrying failures.
        self._completed_v3_plans: set[tuple[str, date]] = set()

    @staticmethod
    def _local_business_date(settings: InventorySettingsModel, moment: datetime) -> date:
        try:
            local = moment.astimezone(ZoneInfo(settings.timezone or "Asia/Ho_Chi_Minh"))
        except Exception:
            local = moment.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
        return local.date() - timedelta(days=1)

    def _v4_settings(self) -> list[InventorySettingsModel]:
        with self.session_factory() as session:
            rows = list(session.scalars(
                select(InventorySettingsModel).where(
                    InventorySettingsModel.enabled.is_(True),
                    InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
                ).order_by(InventorySettingsModel.tenant_id)
            ))
            selected = []
            for row in rows:
                config = row.daily_sheet_config_json
                if not isinstance(config, dict) or config.get("version") != 4:
                    continue
                if self.allowed_tenant_ids is not None and row.tenant_id not in self.allowed_tenant_ids:
                    continue
                session.expunge(row)
                selected.append(row)
            return selected

    @staticmethod
    def _retryable_v4_error(error: Exception) -> bool:
        code = str(error).strip().lower()
        name = type(error).__name__.lower()
        return (
            code == "stale_evidence"
            or any(value in code for value in (
                "429", "rate_limit", "rate limit", "timeout", "timed out",
                "temporarily unavailable", "connection reset", "network",
                "google_transport", "service unavailable", "502", "503", "504",
                "inventory_sheet_agent_v4_missing_tool_call",
                "inventory_sheet_agent_v4_round_limit",
            ))
            or any(value in name for value in ("timeout", "connection", "transport"))
        )

    def _claim_v4_slot(
        self, *, tenant_id: str, business_date: date, slot_kind: str, now: datetime
    ) -> tuple[str, str] | None:
        job_type = V4_SLOT_JOB_TYPES[slot_kind]
        worker_id = f"inventory-v41-{slot_kind}-{uuid4()}"
        repository_types = tuple(V4_SLOT_JOB_TYPES.values())
        with self.session_factory.begin() as session:
            repository = InventoryJobRepository(session, repository_types)
            job = repository.create_job(
                tenant_id=tenant_id,
                job_type=job_type,
                entity_type="inventory_v41_scheduler_slot",
                entity_id=f"{business_date.isoformat()}:{slot_kind}",
                idempotency_key=f"inventory-v41-slot:{business_date.isoformat()}:{slot_kind}",
                payload={"business_date": business_date.isoformat(), "slot_kind": slot_kind},
                max_attempts=V4_SLOT_MAX_ATTEMPTS,
            )
            query = select(InventoryJobModel).where(
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.id == job.id,
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            job = session.scalar(query)
            if job is None or job.status in {"completed", "failed"}:
                return None
            next_attempt_at = job.next_attempt_at
            if next_attempt_at is not None and next_attempt_at.tzinfo is None:
                next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
            lease_expires_at = job.lease_expires_at
            if lease_expires_at is not None and lease_expires_at.tzinfo is None:
                lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
            if job.status == "retry" and next_attempt_at is not None and next_attempt_at > now:
                return None
            if job.status == "processing" and lease_expires_at is not None and lease_expires_at > now:
                return None
            if job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.completed_at = now
                job.last_error_code = "inventory_v41_slot_attempts_exhausted"
                job.last_error_message = "Inventory V4.1 scheduler slot exhausted bounded attempts."
                job.claimed_by = None
                job.claimed_at = None
                job.lease_expires_at = None
                job.updated_at = now
                return None
            job.status = "processing"
            job.claimed_by = worker_id
            job.claimed_at = now
            job.lease_expires_at = now + timedelta(seconds=V4_SLOT_LEASE_SECONDS)
            job.attempt_count += 1
            job.updated_at = now
            session.flush()
            return job.id, worker_id

    def _complete_v4_slot(self, *, tenant_id: str, job_id: str, worker_id: str) -> None:
        with self.session_factory.begin() as session:
            query = select(InventoryJobModel).where(
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.id == job_id,
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            job = session.scalar(query)
            if job is None:
                raise RuntimeError("inventory_v41_slot_missing")
            InventoryJobRepository(session, tuple(V4_SLOT_JOB_TYPES.values())).complete(job, worker_id)

    def _fail_v4_slot(
        self, *, tenant_id: str, job_id: str, worker_id: str,
        error: Exception, retryable: bool, now: datetime,
    ) -> None:
        with self.session_factory.begin() as session:
            query = select(InventoryJobModel).where(
                InventoryJobModel.tenant_id == tenant_id,
                InventoryJobModel.id == job_id,
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            job = session.scalar(query)
            if job is None:
                raise RuntimeError("inventory_v41_slot_missing")
            message = str(error).strip() or type(error).__name__
            InventoryJobRepository(session, tuple(V4_SLOT_JOB_TYPES.values())).fail(
                job,
                worker_id,
                error_code=message if len(message) <= 100 else type(error).__name__,
                error_message=message,
                retryable=retryable,
                now=now,
            )

    def _execute_v4_tenant(
        self, *, settings: InventorySettingsModel, business_date: date,
        slot_kind: str, moment: datetime,
    ) -> int:
        claimed = self._claim_v4_slot(
            tenant_id=settings.tenant_id,
            business_date=business_date,
            slot_kind=slot_kind,
            now=moment,
        )
        if claimed is None:
            return 0
        job_id, worker_id = claimed
        try:
            result = self.sheet_service.run_agent_v4(
                settings.tenant_id, business_date, slot_kind=slot_kind
            )
            if result.status not in V4_SETTLED_RESULTS:
                raise RuntimeError(f"inventory_v41_result_{result.status}")
            self._complete_v4_slot(
                tenant_id=settings.tenant_id,
                job_id=job_id,
                worker_id=worker_id,
            )
            return 1
        except Exception as error:
            retryable = self._retryable_v4_error(error)
            self._fail_v4_slot(
                tenant_id=settings.tenant_id,
                job_id=job_id,
                worker_id=worker_id,
                error=error,
                retryable=retryable,
                now=moment,
            )
            raise

    def execute_v4_slot(self, slot_kind: str, now: datetime | None = None) -> int:
        if slot_kind not in V4_SLOT_KINDS:
            raise ValueError("invalid_inventory_v41_slot_kind")
        moment = now or datetime.now(timezone.utc)
        count = 0
        for settings in self._v4_settings():
            count += self._execute_v4_tenant(
                settings=settings,
                business_date=self._local_business_date(settings, moment),
                slot_kind=slot_kind,
                moment=moment,
            )
        return count

    def run_once(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            settings_rows = list(session.scalars(
                select(InventorySettingsModel).where(
                    InventorySettingsModel.enabled.is_(True),
                    or_(
                        InventorySettingsModel.image_pipeline_enabled.is_(True),
                        InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
                    ),
                ).order_by(InventorySettingsModel.tenant_id)
            ))
            for row in settings_rows:
                session.expunge(row)
        count = 0
        for settings in settings_rows:
            tenant_id = settings.tenant_id
            if self.allowed_tenant_ids is not None and tenant_id not in self.allowed_tenant_ids:
                continue
            try:
                try:
                    local = moment.astimezone(ZoneInfo(settings.timezone or "Asia/Ho_Chi_Minh"))
                except Exception:
                    local = moment.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
                business_date = local.date() - timedelta(days=1)
                if settings.daily_sheet_automation_enabled:
                    snapshot_due = local.time() >= _configured_time(settings.daily_snapshot_time_local, time(5, 50))
                    reconcile_due = local.time() >= _configured_time(settings.daily_reconcile_time_local, time(7, 0))
                    if snapshot_due:
                        is_v3 = (
                            isinstance(settings.daily_sheet_config_json, dict)
                            and settings.daily_sheet_config_json.get("version") == 3
                        )
                        is_v4 = (
                            isinstance(settings.daily_sheet_config_json, dict)
                            and settings.daily_sheet_config_json.get("version") == 4
                        )
                        if is_v4:
                            slot_kind = "reconcile" if reconcile_due else "snapshot"
                            count += self._execute_v4_tenant(
                                settings=settings,
                                business_date=business_date,
                                slot_kind=slot_kind,
                                moment=moment,
                            )
                        elif is_v3:
                            plan_key = (tenant_id, business_date)
                            if plan_key not in self._completed_v3_plans:
                                self.sheet_service.snapshot_and_reset(tenant_id, business_date)
                                self._completed_v3_plans.add(plan_key)
                                count += 1
                        else:
                            snapshot = self.sheet_service.snapshot_and_reset(
                                tenant_id, business_date
                            )
                            if snapshot.status == "completed":
                                count += 1
                                if reconcile_due:
                                    self.sheet_service.reconcile(tenant_id, business_date)
                                    count += 1
                if settings.image_pipeline_enabled:
                    count += self._run_legacy(tenant_id, local)
            except Exception:
                logger.exception("inventory_daily_scheduler_tenant_failed", extra={"tenant_id": tenant_id})
        return count

    def _run_legacy(self, tenant_id: str, local: datetime) -> int:
        count = 0
        due = []
        if local.time() >= time(16, 30): due.append("completeness_check")
        if local.time() >= time(16, 50): due.append("preclose_check")
        for checkpoint in due:
            self.service.evaluate(tenant_id, local.date(), checkpoint=checkpoint)
            count += 1
        if local.time() >= time(17, 0):
            try:
                self.service.finalize(tenant_id, local.date(), actor_id="inventory-scheduler")
                count += 1
            except Exception as exc:
                if type(exc).__name__ != "DailyRunBlocked":
                    raise
        if local.time() >= time(17, 10):
            try:
                self.report_service.generate(tenant_id, local.date())
                count += 1
            except (LookupError, DailyReportNotFinalized):
                pass
        return count
