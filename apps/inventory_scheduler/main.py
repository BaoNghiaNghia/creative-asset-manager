from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings
from app.core.database import SessionLocal
# Register tenant mappings before inventory models with tenant foreign keys flush.
from app.modules.auth_persistence import model as _auth_persistence_models  # noqa: F401
from app.modules.inventory.daily.scheduler import InventoryDailyScheduler

logger = logging.getLogger(__name__)
_stop = False


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory scheduler runtime")
    parser.add_argument("--once", action="store_true", help="Execute one durable V4.1 slot and exit")
    parser.add_argument("--slot", choices=("snapshot", "reconcile"))
    arguments = parser.parse_args(argv)
    if arguments.once != bool(arguments.slot):
        parser.error("--once and --slot must be provided together")
    return arguments


def _handle_signal(_signum, _frame) -> None:
    global _stop
    _stop = True


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    settings = get_settings()
    scheduler = InventoryDailyScheduler(
        SessionLocal,
        allowed_tenant_ids=settings.inventory_tenant_allowlist,
    )
    if arguments.once:
        count = scheduler.execute_v4_slot(arguments.slot)
        print(f"INVENTORY_V41_ONESHOT slot={arguments.slot} executions={count}")
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
