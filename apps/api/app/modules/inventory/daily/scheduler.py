from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker
from app.modules.inventory.daily.service import InventoryDailyRunService
from app.modules.inventory.daily.report import DailyReportNotFinalized, InventoryDailyReportService
from app.modules.inventory.daily_sheet.service import InventoryDailySheetService
from app.modules.inventory.daily_sheet.semantic import build_daily_sheet_semantic_analyzer
from app.modules.inventory.persistence_model import InventorySettingsModel

logger = logging.getLogger(__name__)

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
        self._completed_v4_runs: set[tuple[str, date]] = set()

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
                            run_key = (tenant_id, business_date)
                            if run_key not in self._completed_v4_runs:
                                result = self.sheet_service.run_agent_v4(tenant_id, business_date)
                                if result.status == "completed":
                                    self._completed_v4_runs.add(run_key)
                                    count += 1
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
