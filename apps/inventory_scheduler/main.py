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
    "carry_forward_plan_has_issues",
    "empty_carry_forward_plan",
    "closing_opening_mismatch",
    "unknown_material",
    "unknown_warehouse",
    "inventory_gemini_rate_limited",
    "inventory_gemini_transport_error",
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


def _manual_recovery_scan(
    now: datetime,
    allowed_tenant_ids: frozenset[str],
) -> tuple[list[tuple[str, date, str]], dict[str, object]]:
    candidates: list[tuple[str, date, str]] = []
    diagnostics: dict[str, object] = {
        "v4_tenants": 0,
        "carry_absent": 0,
        "carry_completed": 0,
        "carry_eligible": 0,
        "other_errors": {},
        "plan_issue_codes": {},
    }
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
            diagnostics["v4_tenants"] = int(diagnostics["v4_tenants"]) + 1
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
            if carry is None:
                diagnostics["carry_absent"] = int(diagnostics["carry_absent"]) + 1
                continue
            plan_json = carry.plan_json if isinstance(carry.plan_json, dict) else {}
            plan_issue_codes = diagnostics["plan_issue_codes"]
            assert isinstance(plan_issue_codes, dict)
            for issue in list(plan_json.get("issues") or []):
                code = (
                    str(issue.get("code") or "unknown")
                    if isinstance(issue, dict)
                    else "unknown"
                )
                plan_issue_codes[code] = int(plan_issue_codes.get(code, 0)) + 1
            if carry.status == "completed":
                diagnostics["carry_completed"] = int(diagnostics["carry_completed"]) + 1
                continue
            if carry.error_code in _MANUAL_RECOVERY_ERROR_CODES:
                diagnostics["carry_eligible"] = int(diagnostics["carry_eligible"]) + 1
                candidates.append((
                    settings.tenant_id,
                    business_date,
                    str(carry.error_code),
                ))
                continue
            code = str(carry.error_code or "none")
            other_errors = diagnostics["other_errors"]
            assert isinstance(other_errors, dict)
            other_errors[code] = int(other_errors.get(code, 0)) + 1
    return candidates, diagnostics


def _manual_recovery_candidates(
    now: datetime,
    allowed_tenant_ids: frozenset[str],
) -> list[tuple[str, date]]:
    candidates, _diagnostics = _manual_recovery_scan(now, allowed_tenant_ids)
    return [(tenant_id, business_date) for tenant_id, business_date, _code in candidates]


def _run_manual_recovery_current_day(
    scheduler: InventoryDailyScheduler,
    allowed_tenant_ids: frozenset[str],
    *,
    now: datetime | None = None,
) -> int:
    moment = now or datetime.now(timezone.utc)
    candidates, diagnostics = _manual_recovery_scan(moment, allowed_tenant_ids)
    if not candidates:
        other_errors = diagnostics.get("other_errors") or {}
        if isinstance(other_errors, dict):
            other_error_summary = ",".join(
                f"{code}:{count}" for code, count in sorted(other_errors.items())
            ) or "none"
        else:
            other_error_summary = "invalid"
        plan_issue_codes = diagnostics.get("plan_issue_codes") or {}
        if isinstance(plan_issue_codes, dict):
            issue_summary = ",".join(
                f"{code}:{count}" for code, count in sorted(plan_issue_codes.items())
            ) or "none"
        else:
            issue_summary = "invalid"
        print(
            "INVENTORY_MANUAL_RECOVERY status=noop "
            "reason=no_eligible_current_day "
            f"v4_tenants={diagnostics.get('v4_tenants', 0)} "
            f"carry_absent={diagnostics.get('carry_absent', 0)} "
            f"carry_completed={diagnostics.get('carry_completed', 0)} "
            f"carry_eligible={diagnostics.get('carry_eligible', 0)} "
            f"other_errors={other_error_summary} "
            f"plan_issue_codes={issue_summary}"
        )
        return 0
    if len(candidates) != 1:
        print(
            "INVENTORY_MANUAL_RECOVERY status=blocked "
            f"reason=ambiguous_candidates count={len(candidates)}"
        )
        return 2

    tenant_id, business_date, initial_error_code = candidates[0]
    preview_only = initial_error_code in {
        "carry_forward_plan_has_issues",
        "empty_carry_forward_plan",
        "closing_opening_mismatch",
        "unknown_material",
        "unknown_warehouse",
    }
    try:
        preview = scheduler.preview_v4_morning_reset_recovery(
            tenant_id,
            business_date,
            moment,
        )
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__))
        _fresh_candidates, fresh_diagnostics = _manual_recovery_scan(
            moment, allowed_tenant_ids
        )
        plan_issue_codes = fresh_diagnostics.get("plan_issue_codes") or {}
        if isinstance(plan_issue_codes, dict):
            issue_summary = ",".join(
                f"{issue_code}:{count}"
                for issue_code, count in sorted(plan_issue_codes.items())
            ) or "none"
        else:
            issue_summary = "invalid"
        print(
            "INVENTORY_MANUAL_RECOVERY status=blocked "
            f"stage=preview business_date={business_date.isoformat()} "
            f"error_code={code} plan_issue_codes={issue_summary}"
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
    if preview_only:
        print(
            "INVENTORY_MANUAL_RECOVERY status=preview_only "
            "reason=prior_plan_issues_requires_second_pass "
            f"business_date={business_date.isoformat()} plan_hash={plan_hash}"
        )
        return 0

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
