from __future__ import annotations

import asyncio
import logging
import signal
import threading

from sqlalchemy import select, text

from app.core.config import Settings
from app.core.database import SessionLocal
from app.modules.inventory.config import InventoryWorkerConfig
from app.modules.inventory.jobs.errors import InventoryJobFailure
from app.modules.inventory.drive.poller import poll_inventory_drive
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.health import InventoryWorkerHealth, InventoryWorkerHealthServer
from app.modules.inventory.jobs.registry import build_inventory_handler_registry
from app.modules.inventory.persistence_model import InventorySourceFileModel
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
    registry = build_inventory_handler_registry(runtime_settings)

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
            if config.drive_poller_enabled:
                try:
                    asyncio.run(poll_inventory_drive(runtime_settings))
                except Exception:
                    logger.exception("inventory_drive_poll_runtime_failed")
            with SessionLocal() as session:
                repository = InventoryJobRepository(session, registry.job_types)
                job = repository.claim_next(
                    worker_id=config.worker_id,
                    lease_seconds=config.lease_seconds,
                )
                if job is None:
                    continue
                session.commit()
                job_id = job.id
                handler = registry.resolve(job.job_type)
            failure = None
            try:
                if handler is None:
                    raise InventoryJobFailure(
                        "inventory_handler_not_registered", retryable=False
                    )
                handler(job)
            except InventoryJobFailure as exc:
                failure = exc
            except Exception:
                logger.exception(
                    "inventory_job_unexpected_failure job_id=%s job_type=%s",
                    job.id,
                    job.job_type,
                )
                failure = InventoryJobFailure(
                    "inventory_job_unexpected_failure", retryable=True
                )
            with SessionLocal() as session:
                current = session.get(InventoryJobModel, job_id)
                if current is None:
                    continue
                repository = InventoryJobRepository(session, registry.job_types)
                if failure is None:
                    repository.complete(current, config.worker_id)
                else:
                    will_retry = repository.fail(
                        current,
                        config.worker_id,
                        error_code=failure.code,
                        error_message=failure.code,
                        retryable=failure.retryable,
                    )
                    if failure.retryable and not will_retry:
                        source_file_id = str(
                            (current.payload_json or {}).get("source_file_id")
                            or current.entity_id
                        )
                        source = session.scalar(
                            select(InventorySourceFileModel).where(
                                InventorySourceFileModel.tenant_id == current.tenant_id,
                                InventorySourceFileModel.id == source_file_id,
                            )
                        )
                        if source is not None and source.status == "retryable_failure":
                            source.status = "terminal_failure"
                session.commit()
        return 0
    finally:
        health.stop()
        health_server.close()
