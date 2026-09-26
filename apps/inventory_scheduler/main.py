from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings
from app.core.database import SessionLocal
# Register tenant mappings before inventory models with tenant foreign keys flush.
from app.modules.auth_persistence import model as _auth_persistence_models  # noqa: F401
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler
from app.modules.inventory.persistence_model import (
    InventoryDailyCarryForwardModel,
    InventorySettingsModel,
)

logger = logging.getLogger(__name__)
_stop = False

_MANUAL_RECOVERY_ERROR_CODES = frozenset({
    "previous_day_gemini_not_verified",
    "inventory_morning_reset_manual_recovery_preview_ready",
    "stale_evidence",
})


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory scheduler runtime")
    parser.add_argument("--once", action="store_true", help="Evaluate the configured daily lifecycle once and exit")
    parser.add_argument("--slot", choices=("snapshot", "reconcile"))
    parser.add_argument(
        "--manual-recovery-current-day",
        action="store_true",
        help="Preview and apply exactly one eligible current-day Morning Reset recovery.",
    )
    arguments = parser.parse_args(argv)
    if arguments.slot and not arguments.once:
        parser.error("--slot requires --once")
    if arguments.manual_recovery_current_day and (arguments.once or arguments.slot):
        parser.error("--manual-recovery-current-day cannot be combined with --once/--slot")
    return arguments


def _handle_signal(_signum, _frame) -> None:
    global _stop
    _stop = True


def _manual_recovery_candidates(
    now: datetime,
    allowed_tenant_ids: frozenset[str],
) -> list[tuple[str, date]]:
    candidates: list[tuple[str, date]] = []
    with SessionLocal() as session:
        settings_rows = list(session.scalars(select(InventorySettingsModel)))
        for settings in settings_rows:
            if allowed_tenant_ids and settings.tenant_id not in allowed_tenant_ids:
                continue
            config = settings.daily_sheet_config_json
            if (
                not settings.daily_sheet_automation_enabled
                or not isinstance(config, dict)
                or config.get("version") != 4
            ):
                continue
            business_date = now.astimezone(
                ZoneInfo(settings.timezone or "Asia/Ho_Chi_Minh")
            ).date()
            carry = session.scalar(
                select(InventoryDailyCarryForwardModel).where(
                    InventoryDailyCarryForwardModel.tenant_id
                    == settings.tenant_id,
                    InventoryDailyCarryForwardModel.target_business_date
                    == business_date,
                )
            )
            if (
                carry is not None
                and carry.status != "completed"
                and carry.error_code in _MANUAL_RECOVERY_ERROR_CODES
            ):
                candidates.append((settings.tenant_id, business_date))
    return candidates


def _run_manual_recovery_current_day(
    scheduler: InventoryDailyScheduler,
    allowed_tenant_ids: frozenset[str],
    *,
    now: datetime | None = None,
) -> int:
    moment = now or datetime.now(timezone.utc)
    candidates = _manual_recovery_candidates(moment, allowed_tenant_ids)
    if not candidates:
        print("INVENTORY_MANUAL_RECOVERY status=noop reason=no_eligible_current_day")
        return 0
    if len(candidates) != 1:
        print(
            "INVENTORY_MANUAL_RECOVERY status=blocked "
            f"reason=ambiguous_candidates count={len(candidates)}"
        )
        return 2

    tenant_id, business_date = candidates[0]
    try:
        preview = scheduler.preview_v4_morning_reset_recovery(
            tenant_id,
            business_date,
            moment,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        print(
            "INVENTORY_MANUAL_RECOVERY status=blocked "
            f"stage=preview business_date={business_date.isoformat()} "
            f"error_code={code}"
        )
        return 2

    if preview.get("status") == "completed":
        print(
            "INVENTORY_MANUAL_RECOVERY status=noop "
            f"reason=already_completed business_date={business_date.isoformat()}"
        )
        return 0

    operations = preview.get("operations")
    plan_hash = str(preview.get("plan_hash") or "")
    safe_count = int(preview.get("safe_operation_count") or 0)
    write_count = int(preview.get("write_operation_count") or 0)
    excluded_clear_count = int(preview.get("excluded_clear_count") or 0)
    if (
        preview.get("status") != "preview_ready"
        or len(plan_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in plan_hash)
        or safe_count <= 0
        or not isinstance(operations, list)
        or len(operations) != safe_count
        or any(
            not isinstance(operation, dict)
            or not operation.get("sheet")
            or not operation.get("cell")
            or not operation.get("source_sheet")
            or not operation.get("source_cell")
            or not isinstance(operation.get("needs_write"), bool)
            for operation in operations
        )
    ):
        print(
            "INVENTORY_MANUAL_RECOVERY status=blocked "
            f"stage=preview business_date={business_date.isoformat()} "
            "error_code=inventory_manual_recovery_preview_invalid"
        )
        return 2

    print(
        "INVENTORY_MANUAL_RECOVERY status=preview_ready "
        f"business_date={business_date.isoformat()} "
        f"safe_operations={safe_count} writes={write_count} "
        f"excluded_clears={excluded_clear_count} plan_hash={plan_hash}"
    )

    try:
        result = scheduler.apply_v4_morning_reset_recovery(
            tenant_id,
            business_date,
            plan_hash=plan_hash,
            now=moment,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        print(
            "INVENTORY_MANUAL_RECOVERY status=blocked "
            f"stage=apply business_date={business_date.isoformat()} "
            f"error_code={code} plan_hash={plan_hash}"
        )
        return 2

    print(
        "INVENTORY_MANUAL_RECOVERY status=completed "
        f"business_date={business_date.isoformat()} "
        f"applied={int(result.get('applied_count') or 0)} "
        f"already_correct={int(result.get('already_correct_count') or 0)} "
        f"excluded_clears={int(result.get('excluded_clear_count') or 0)} "
        f"plan_hash={plan_hash}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    settings = get_settings()
    scheduler = InventoryDailyScheduler(
        SessionLocal,
        allowed_tenant_ids=settings.inventory_tenant_allowlist,
    )
    if arguments.manual_recovery_current_day:
        return _run_manual_recovery_current_day(
            scheduler,
            settings.inventory_tenant_allowlist,
        )
    if arguments.once:
        if arguments.slot:
            count = scheduler.execute_v4_slot(arguments.slot)
            print(f"INVENTORY_V41_ONESHOT slot={arguments.slot} executions={count}")
        else:
            count = scheduler.run_once()
            print(f"INVENTORY_V5J_ONESHOT executions={count}")
        return 0
    if not settings.INVENTORY_DAILY_SCHEDULER_ENABLED:
        logger.info("inventory_daily_scheduler_disabled")
        return 0
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    logger.info("inventory_daily_scheduler_started")
    while not _stop:
        scheduler.run_once()
        for _ in range(settings.INVENTORY_DAILY_SCHEDULER_POLL_SECONDS):
            if _stop:
                break
            time.sleep(1)
    logger.info("inventory_daily_scheduler_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
