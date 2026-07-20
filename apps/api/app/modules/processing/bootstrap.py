from __future__ import annotations

import json
import logging
import os
import signal
import socket
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import SessionLocal, engine
from app.domain.processing.handlers import WorkerDependencies
from app.modules.processing.health import WorkerHealthServer, WorkerHealthState
from app.modules.ai_metadata.handler import AssetAnalyzeJobHandler
from app.modules.processing.registry import build_handler_registry
from app.modules.processing.runtime import WorkerRuntime, WorkerRuntimeConfig
from app.providers.ai.unconfigured import UnconfiguredAiMetadataProvider
from app.providers.ai.gemini import GeminiAiMetadataProvider
from app.providers.google.storage import GoogleDriveAssetStorage
from app.providers.source_factory import create_source_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


_STANDARD_LOG_FIELDS = set(logging.makeLogRecord({}).__dict__)


class JsonWorkerLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_worker_logging(level: str) -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonWorkerLogFormatter())
    logger = logging.getLogger("cam.worker")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def probe_database(session_factory: Callable[[], Session]) -> None:
    with session_factory() as session:
        session.execute(text("SELECT 1"))


def build_worker_runtime(
    settings: Settings,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    logger: logging.Logger | None = None,
    dependency_closers: tuple[Callable[[], Any], ...] = (),
) -> WorkerRuntime:
    worker_id = settings.WORKER_ID or default_worker_id()
    probe_database(session_factory)
    storage_provider = UnconfiguredAssetStorageProvider()
    if (
        settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN
        and settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID
    ):
        storage_provider = GoogleDriveAssetStorage(
            settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN,
            root_folder_id=settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID,
        )
    ai_provider = UnconfiguredAiMetadataProvider()
    if settings.GEMINI_API_KEY:
        ai_provider = GeminiAiMetadataProvider(
            settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
            timeout_seconds=settings.GEMINI_TIMEOUT_SECONDS,
        )
    dependencies = WorkerDependencies(
        session_factory=session_factory,
        source_provider_factory=create_source_provider,
        storage_provider=storage_provider,
        ai_provider=ai_provider,
        closers=dependency_closers,
    )
    return WorkerRuntime(
        config=WorkerRuntimeConfig(
            worker_id=worker_id,
            enabled=settings.PROCESSING_JOBS_ENABLED,
            lease_seconds=settings.WORKER_LEASE_SECONDS,
            heartbeat_seconds=settings.WORKER_HEARTBEAT_SECONDS,
            idle_poll_seconds=settings.WORKER_IDLE_POLL_SECONDS,
            drain_timeout_seconds=settings.WORKER_DRAIN_TIMEOUT_SECONDS,
        ),
        dependencies=dependencies,
        registry=build_handler_registry(
            (("asset_analyze", AssetAnalyzeJobHandler(settings)),)
        ),
        health=WorkerHealthState(worker_id),
        logger=logger,
    )


def run_worker(
    settings: Settings,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    logger: logging.Logger | None = None,
    health_server_factory: Callable[[WorkerHealthState, str, int], WorkerHealthServer] = WorkerHealthServer,
    install_signal_handlers: bool = True,
    dependency_closers: tuple[Callable[[], Any], ...] = (),
) -> int:
    worker_logger = logger or configure_worker_logging(settings.WORKER_LOG_LEVEL)
    runtime: WorkerRuntime | None = None
    health_server: WorkerHealthServer | None = None
    try:
        runtime = build_worker_runtime(
            settings,
            session_factory=session_factory,
            logger=worker_logger,
            dependency_closers=dependency_closers,
        )
        health_server = health_server_factory(
            runtime.health,
            settings.WORKER_HEALTH_HOST,
            settings.WORKER_HEALTH_PORT,
        )
        health_server.start()

        if install_signal_handlers:
            def stop(_signum: int, _frame: object) -> None:
                runtime.request_shutdown()

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)

        worker_logger.info(
            "worker_configuration",
            extra={
                "worker_id": runtime.config.worker_id,
                "processing_enabled": runtime.config.enabled,
                "health_host": settings.WORKER_HEALTH_HOST,
                "health_port": settings.WORKER_HEALTH_PORT,
                "lease_seconds": runtime.config.lease_seconds,
                "heartbeat_seconds": runtime.config.heartbeat_seconds,
                "drain_timeout_seconds": runtime.config.drain_timeout_seconds,
                "registered_job_types": runtime.registry.job_types,
            },
        )
        runtime.run_forever()
        return 0
    except Exception as exc:
        worker_logger.critical(
            "worker_startup_failed",
            extra={
                "worker_id": settings.WORKER_ID or "uninitialized",
                "error_code": type(exc).__name__,
                "error_message": "Worker dependency initialization failed.",
            },
        )
        return 1
    finally:
        if runtime is not None:
            runtime.close()
        if health_server is not None:
            health_server.close()


def run_default_worker() -> int:
    settings = Settings()
    return run_worker(settings, dependency_closers=(engine.dispose,))
