from __future__ import annotations

import json
import logging
import os
import signal
import socket
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.redaction import redact_url_queries, sanitize_log_value
from app.core.database import SessionLocal, engine
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.domain.processing.handlers import WorkerDependencies
from app.modules.processing.health import WorkerHealthServer, WorkerHealthState
from app.modules.ai_metadata.handler import AssetAnalyzeJobHandler
from app.modules.ai_batch.handlers import (
    AiBatchImportJobHandler, AiBatchPollJobHandler,
    AiBatchPrepareJobHandler, AiBatchRetryItemsJobHandler,
    AiBatchSubmitJobHandler,
)
from app.modules.pipeline.content_resolver import (
    SourceAssetPipelineContentResolver,
)
from app.modules.pipeline.stages import (
    ProviderDownloadStage,
    ProviderStorageStage,
)
from app.modules.pipeline.handlers import (
    AssetIndexJobHandler,
    AssetStoreJobHandler,
    MetadataSidecarExportJobHandler,
    SearchProjectionBuildJobHandler,
    SourceAssetDownloadJobHandler,
)
from app.modules.processing.registry import build_handler_registry
from app.modules.retention.handler import RetentionCleanupJobHandler
from app.modules.retention.scheduler import RetentionCleanupScheduler
from app.modules.processing.runtime import WorkerRuntime, WorkerRuntimeConfig
from app.modules.source_sync.handler import SourceSyncJobHandler
from app.providers.ai.factory import build_ai_provider_registry
from app.providers.google.storage import GoogleDriveAssetStorage
from app.providers.source_factory import create_source_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


_STANDARD_LOG_FIELDS = set(logging.makeLogRecord({}).__dict__)

_JOB_GLOBAL_FLAGS: dict[str, tuple[str, ...]] = {
    "source_sync": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "INCREMENTAL_SOURCE_SYNC_ENABLED"),
    "source_asset_download": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "CONTENT_DEDUP_ENABLED"),
    "asset_store": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "MANAGED_ASSET_STORAGE_ENABLED"),
    "asset_analyze": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_SINGLE_ANALYSIS_ENABLED"),
    "ai_batch_prepare": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_BATCH_ANALYSIS_ENABLED"),
    "ai_batch_submit": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_BATCH_ANALYSIS_ENABLED"),
    "ai_batch_poll": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_BATCH_ANALYSIS_ENABLED"),
    "ai_batch_import": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_BATCH_ANALYSIS_ENABLED"),
    "ai_batch_retry_items": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DYNAMIC_AI_METADATA_ENABLED", "AI_BATCH_ANALYSIS_ENABLED"),
    "search_projection_build": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "SEARCH_PROJECTION_ENABLED"),
    "asset_index": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "ELASTICSEARCH_V2_ENABLED"),
    "metadata_sidecar_export": ("PROCESSING_JOBS_ENABLED", "UNIFIED_ASSET_INGESTION_ENABLED", "DRIVE_METADATA_SIDECAR_ENABLED"),
    "retention_cleanup": ("PROCESSING_JOBS_ENABLED", "RETENTION_CLEANUP_ENABLED"),
}

def globally_enabled_job_types(settings: Settings) -> tuple[str, ...]:
    def enabled(job_type: str, flags: tuple[str, ...]) -> bool:
        if job_type == "asset_index":
            return (
                settings.PROCESSING_JOBS_ENABLED
                and settings.UNIFIED_ASSET_INGESTION_ENABLED
                and (settings.ELASTICSEARCH_V2_ENABLED or settings.SEARCH_V3_ENABLED)
            )
        return all(bool(getattr(settings, flag)) for flag in flags)

    return tuple(
        job_type for job_type, flags in _JOB_GLOBAL_FLAGS.items()
        if enabled(job_type, flags)
        and not (settings.AI_EMERGENCY_STOP_ENABLED and job_type.startswith(("asset_analyze", "ai_batch_")))
    )



class JsonWorkerLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "event": redact_url_queries(record.getMessage()),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_FIELDS and key not in {"message", "asctime"}:
                payload[key] = sanitize_log_value(value)
        if record.exc_info:
            payload["exception"] = redact_url_queries(
                self.formatException(record.exc_info)
            )
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
    resources: Mapping[str, Any] | None = None,
) -> WorkerRuntime:
    worker_id = settings.WORKER_ID or default_worker_id()
    probe_database(session_factory)
    if settings.PROCESSING_JOBS_ENABLED and settings.RETENTION_CLEANUP_ENABLED:
        RetentionCleanupScheduler(session_factory, settings).schedule_known_tenants()
    storage_provider = UnconfiguredAssetStorageProvider()
    if (
        (
            settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN
            or settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN
        )
        and settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID
    ):
        storage_provider = GoogleDriveAssetStorage(
            settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN,
            root_folder_id=settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID,
            refresh_token=settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN,
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )
    storage_configured = not isinstance(
        storage_provider, UnconfiguredAssetStorageProvider
    )
    if settings.MANAGED_ASSET_STORAGE_ENABLED and not storage_configured:
        raise RuntimeError("managed asset storage is enabled but not configured")
    ai_provider_registry = build_ai_provider_registry(settings, session_factory=session_factory)
    resolver = SourceAssetPipelineContentResolver(session_factory)
    default_resources: dict[str, Any] = {
        "pipeline_download_stage": ProviderDownloadStage(
            session_factory, resolver
        ),
    }
    if storage_configured:
        default_resources["pipeline_storage_stage"] = ProviderStorageStage(
            session_factory, resolver, storage_provider
        )
    if settings.ELASTICSEARCH_URL:
        default_resources["search_index_provider"] = ElasticsearchV3Index(
            ElasticsearchV3Config(
                settings.ELASTICSEARCH_URL,
                settings.ELASTICSEARCH_INDEX_PREFIX,
                index_generation="v3" if settings.SEARCH_V3_ENABLED else "v2",
            )
        )
    # Explicit resources are deliberate test/operational overrides.
    default_resources.update(resources or {})
    dependencies = WorkerDependencies(
        session_factory=session_factory,
        source_provider_factory=create_source_provider,
        storage_provider=storage_provider,
        ai_provider_registry=ai_provider_registry,
        closers=dependency_closers,
        resources=default_resources,
    )
    return WorkerRuntime(
        config=WorkerRuntimeConfig(
            worker_id=worker_id,
            enabled=settings.PROCESSING_JOBS_ENABLED,
            lease_seconds=settings.WORKER_LEASE_SECONDS,
            heartbeat_seconds=settings.WORKER_HEARTBEAT_SECONDS,
            idle_poll_seconds=settings.WORKER_IDLE_POLL_SECONDS,
            drain_timeout_seconds=settings.WORKER_DRAIN_TIMEOUT_SECONDS,
            enforce_tenant_policy=True,
            allowed_job_types=globally_enabled_job_types(settings),
        ),
        dependencies=dependencies,
        registry=build_handler_registry(
            (
                ("source_sync", SourceSyncJobHandler(settings)),
                ("source_asset_download", SourceAssetDownloadJobHandler(settings)),
                ("asset_store", AssetStoreJobHandler(settings)),
                ("asset_analyze", AssetAnalyzeJobHandler(settings)),
                ("ai_batch_prepare", AiBatchPrepareJobHandler(settings)),
                ("ai_batch_submit", AiBatchSubmitJobHandler(settings)),
                ("ai_batch_poll", AiBatchPollJobHandler(settings)),
                ("ai_batch_import", AiBatchImportJobHandler(settings)),
                ("ai_batch_retry_items", AiBatchRetryItemsJobHandler(settings)),
                ("search_projection_build", SearchProjectionBuildJobHandler(settings)),
                ("asset_index", AssetIndexJobHandler(settings)),
                ("metadata_sidecar_export", MetadataSidecarExportJobHandler(settings)),
                ("retention_cleanup", RetentionCleanupJobHandler(settings)),
            )
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

        runtime_resources = getattr(
            getattr(runtime, "dependencies", None),
            "resources",
            {},
        )
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
                "pipeline_download_stage_configured": (
                    "pipeline_download_stage"
                    in runtime_resources
                ),
                "pipeline_storage_stage_configured": (
                    "pipeline_storage_stage"
                    in runtime_resources
                ),
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
