from __future__ import annotations

import logging
import signal
import threading

from sqlalchemy import text

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.inventory.config import InventoryWorkerConfig
from app.modules.inventory.health import InventoryWorkerHealth, InventoryWorkerHealthServer
from app.modules.inventory.jobs.registry import build_inventory_handler_registry
from app.modules.inventory.jobs.repository import InventoryJobRepository


def run_inventory_worker(settings: Settings | None = None) -> int:
    runtime_settings = settings or Settings()
    config = InventoryWorkerConfig.from_settings(runtime_settings)
    logger = logging.getLogger("cam.inventory_worker")
    logging.basicConfig(level=logging.INFO)
    stop_event = threading.Event()
    health = InventoryWorkerHealth(config.worker_id)
    health_server = InventoryWorkerHealthServer(
        health, config.health_host, config.health_port
    )
    registry = build_inventory_handler_registry()

    def stop(_signum: int, _frame: object) -> None:
        health.start_draining()
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    health_server.start()
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        health.mark_ready(config.enabled)
        logger.info(
            "inventory_worker_started worker_id=%s enabled=%s registered_job_types=%s",
            config.worker_id,
            config.enabled,
            registry.job_types,
        )
        while not stop_event.wait(config.idle_poll_seconds):
            if not config.enabled:
                continue
            with SessionLocal() as session:
                job = InventoryJobRepository(
                    session, registry.job_types
                ).claim_next(
                    worker_id=config.worker_id,
                    lease_seconds=config.lease_seconds,
                )
                if job is not None:
                    raise RuntimeError(
                        "Inventory job was claimed without a registered Phase 1 handler"
                    )
        return 0
    finally:
        health.stop()
        health_server.close()
