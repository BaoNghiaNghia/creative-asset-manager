from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.modules.assets.model import ExternalSourceModel
from app.modules.creative_pipeline.discovery import CreativePipelineDiscoveryScanner
from app.modules.creative_pipeline.rollout import CreativePipelineRolloutPolicy
from app.modules.processing.repository import ProcessingRepository

JOB_TYPE = "creative_pipeline_scan"
ENTITY_TYPE = "creative_pipeline_canary_root"


class CreativePipelineCanaryScheduler:
    """Produces one durable, idempotent daily root-scan job for the default tenant."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings, *, logger=None):
        self.session_factory, self.settings = session_factory, settings
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread = None

    @property
    def enabled(self):
        return bool(self.settings.PROCESSING_JOBS_ENABLED and self.settings.CREATIVE_PIPELINE_CANARY_ENABLED and self.settings.CREATIVE_PIPELINE_CANARY_ROOT_FOLDER_ID.strip() and self.settings.AUTH_DEFAULT_TENANT_ID.strip())

    def _date(self, now):
        local = now.astimezone(ZoneInfo(self.settings.CREATIVE_PIPELINE_CANARY_TIMEZONE))
        return local.date().isoformat() if local.hour >= self.settings.CREATIVE_PIPELINE_CANARY_SCAN_HOUR else None

    def tick(self, *, now=None):
        now = now or datetime.now(timezone.utc)
        date = self._date(now)
        if not self.enabled or not date:
            return None
        tenant_id = self.settings.AUTH_DEFAULT_TENANT_ID.strip()
        policy = CreativePipelineRolloutPolicy.from_settings(self.settings)
        if policy.enabled and tenant_id not in policy.tenant_ids:
            return None
        with self.session_factory() as session:
            statement = select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.source_type == "google_drive",
                ExternalSourceModel.status == "active",
            )
            if policy.enabled:
                statement = statement.where(ExternalSourceModel.id.in_(policy.external_source_ids))
            source = session.scalar(statement.order_by(ExternalSourceModel.id).limit(1))
            if source is None:
                return None
            job = ProcessingRepository(session).create_job(
                tenant_id=tenant_id, job_type=JOB_TYPE, entity_type=ENTITY_TYPE,
                entity_id=self.settings.CREATIVE_PIPELINE_CANARY_ROOT_FOLDER_ID.strip(),
                idempotency_key=f"creative-pipeline-canary:{tenant_id}:{date}",
                payload={"external_source_id": source.id, "root_folder_id": self.settings.CREATIVE_PIPELINE_CANARY_ROOT_FOLDER_ID.strip(), "scheduled_date": date},
                priority=100, max_attempts=3, provider_key="google_drive", provider_scope="creative_pipeline_canary",
            )
            session.commit()
            return job

    def start(self):
        if not self.enabled or self._thread is not None: return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="creative-pipeline-canary", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try: self.tick()
            except Exception: self.logger.exception("creative_pipeline_canary_tick_failed")
            self._stop.wait(self.settings.CREATIVE_PIPELINE_CANARY_POLL_INTERVAL_SECONDS)

    def stop(self, timeout=None):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None: thread.join(timeout or self.settings.CREATIVE_PIPELINE_CANARY_POLL_INTERVAL_SECONDS + 1)


class CreativePipelineCanaryScanHandler:
    def __init__(self, settings=None): self.settings = settings

    def __call__(self, context: JobHandlerContext):
        settings = self.settings or get_settings()
        if not settings.CREATIVE_PIPELINE_CANARY_ENABLED:
            return JobHandlerResult.non_retryable("creative_pipeline_canary_disabled", "Creative Pipeline canary is disabled.")
        payload = context.job.payload
        source_id, root_id = payload.get("external_source_id"), payload.get("root_folder_id")
        if not isinstance(source_id, str) or not isinstance(root_id, str) or not root_id:
            return JobHandlerResult.non_retryable("creative_pipeline_scan_payload_invalid", "Google Drive root scan payload is invalid.")
        if context.is_cancelled or context.shutdown_requested.is_set(): return JobHandlerResult.cancelled()
        policy = CreativePipelineRolloutPolicy.from_settings(settings)
        if policy.enabled and not policy.allows_source(
            tenant_id=context.job.tenant_id, external_source_id=source_id,
        ):
            return JobHandlerResult.non_retryable(
                "creative_pipeline_rollout_source_denied",
                "Creative Pipeline source is outside the configured rollout.",
            )
        try:
            with context.dependencies.session_factory() as session:
                scanner = CreativePipelineDiscoveryScanner(session, rollout_policy=policy)
                asyncio.run(scanner.scan_authorized(
                    tenant_id=context.job.tenant_id, external_source_id=source_id,
                    root_folder_id=root_id,
                ))
            return JobHandlerResult.completed()
        except ValueError as exc:
            return JobHandlerResult.non_retryable("creative_pipeline_scan_source_unavailable", str(exc))
        except Exception:
            return JobHandlerResult.retryable("creative_pipeline_scan_failed", "Creative Pipeline Google Drive scan failed.")
