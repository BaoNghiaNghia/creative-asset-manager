from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.service import INVENTORY_TIMEZONE, InventoryDailyRunService
from app.modules.inventory.persistence_model import InventorySettingsModel

logger = logging.getLogger(__name__)


class InventoryDailyScheduler:
    """A small, independently deployable Inventory scheduler.

    It is deliberately not a worker handler and uses persisted daily-event
    idempotency keys so restarts and concurrent scheduler processes are safe.
    """

    def __init__(self, session_factory: sessionmaker[Session], service: InventoryDailyRunService | None = None, *, allowed_tenant_ids: frozenset[str] | None = None):
        self.session_factory = session_factory
        self.service = service or InventoryDailyRunService(session_factory)
        self.allowed_tenant_ids = allowed_tenant_ids

    def run_once(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(timezone.utc)
        local = moment.astimezone(INVENTORY_TIMEZONE)
        due: list[str] = []
        if local.time() >= time(16, 30):
            due.append("completeness_check")
        if local.time() >= time(16, 50):
            due.append("preclose_check")
        finalize = local.time() >= time(17, 0)
        if not due and not finalize:
            return 0
        with self.session_factory() as session:
            tenants = list(session.scalars(
                select(InventorySettingsModel.tenant_id).where(InventorySettingsModel.enabled.is_(True))
            ))
        count = 0
        for tenant_id in tenants:
            if self.allowed_tenant_ids is not None and tenant_id not in self.allowed_tenant_ids:
                continue
            try:
                for checkpoint in due:
                    self.service.evaluate(tenant_id, local.date(), checkpoint=checkpoint)
                    count += 1
                if finalize:
                    try:
                        self.service.finalize(tenant_id, local.date(), actor_id="inventory-scheduler")
                        count += 1
                    except Exception as exc:
                        # A not-ready day is normal at 17:00; retain its audit
                        # snapshot and continue other tenants.
                        if type(exc).__name__ != "DailyRunBlocked":
                            raise
            except Exception:
                logger.exception("inventory_daily_scheduler_tenant_failed", extra={"tenant_id": tenant_id})
        return count
