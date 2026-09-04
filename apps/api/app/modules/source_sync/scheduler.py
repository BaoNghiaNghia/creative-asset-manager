from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.source_state import is_external_source_decommissioned
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.assets.source_credentials import source_credential_contract
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.modules.processing_policy.service import ProcessingPolicyService
from app.modules.source_sync.repository import SourceSyncRepository

ACTIVE_JOB_STATUSES = ("pending", "processing", "retry", "queued", "running", "claimed")

@dataclass(frozen=True, slots=True)
class SourceSyncScheduleResult:
    tenant_id: str
    source_id: str
    mode: str | None
    job_id: str | None
    created: bool
    skipped_reason: str | None = None

class SourceSyncScheduler:
    """Periodic producer for the existing durable source_sync job queue."""
    def __init__(self, session_factory: Callable[[], Session], settings: Settings, *, logger: logging.Logger | None = None):
        self.session_factory = session_factory
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.PROCESSING_JOBS_ENABLED and self.settings.INCREMENTAL_SOURCE_SYNC_ENABLED and self.settings.SOURCE_SYNC_SCHEDULER_ENABLED)

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="source-sync-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout if timeout is not None else self.settings.SOURCE_SYNC_POLL_INTERVAL_SECONDS + 1)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.logger.exception("source_sync_scheduler_tick_failed")
            self._stop.wait(self.settings.SOURCE_SYNC_POLL_INTERVAL_SECONDS)

    def _source_credentials(self, session: Session, source: ExternalSourceModel) -> OAuthConnectionModel | None:
        if source.status != "active" or not source.oauth_connection_id:
            return None
        try:
            contract = source_credential_contract(source.source_type)
        except ValueError:
            return None
        connection = session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.id == source.oauth_connection_id,
            OAuthConnectionModel.tenant_id == source.tenant_id,
            OAuthConnectionModel.provider == contract.provider,
            OAuthConnectionModel.connection_purpose == contract.connection_purpose,
            OAuthConnectionModel.status == "active",
            OAuthConnectionModel.revoked_at.is_(None),
        ).limit(1))
        return connection if connection is not None and (connection.access_token_ciphertext or connection.refresh_token_ciphertext) else None

    def _active_job(self, session: Session, tenant_id: str, source_id: str, now: datetime) -> ProcessingJobModel | None:
        stale_cutoff = now - timedelta(seconds=self.settings.SOURCE_SYNC_JOB_STALE_SECONDS)
        running = ProcessingJobModel.status.in_(("processing", "running", "claimed"))
        return session.scalar(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.job_type == "source_sync", ProcessingJobModel.entity_type == "external_source", ProcessingJobModel.entity_id == source_id, or_(ProcessingJobModel.status.in_(("pending", "retry", "queued")), running & or_(ProcessingJobModel.lease_expires_at > now, ProcessingJobModel.updated_at >= stale_cutoff))).order_by(ProcessingJobModel.created_at.desc()).limit(1))

    def enqueue_source(self, tenant_id: str, source_id: str, *, now: datetime | None = None, full: bool = False, dry_run: bool = False) -> SourceSyncScheduleResult:
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            source = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == tenant_id, ExternalSourceModel.id == source_id))
            if source is None:
                return SourceSyncScheduleResult(tenant_id, source_id, None, None, False, "source_not_found")
            if is_external_source_decommissioned(source):
                session.rollback()
                return SourceSyncScheduleResult(tenant_id, source_id, None, None, False, "source_decommissioned")
            if self._source_credentials(session, source) is None:
                return SourceSyncScheduleResult(tenant_id, source_id, None, None, False, "credentials_unavailable")
            policy = ProcessingPolicyService(ProcessingPolicyRepository(session), self.settings).effective(tenant_id)
            if not policy.effective.get("source_sync_enabled", False):
                session.rollback()
                return SourceSyncScheduleResult(tenant_id, source_id, None, None, False, "tenant_policy_disabled_or_paused")
            active = self._active_job(session, tenant_id, source_id, current)
            if active is not None:
                session.rollback()
                return SourceSyncScheduleResult(tenant_id, source_id, None, active.id, False, "active_job")
            cursor = SourceSyncRepository(session).get_cursor(tenant_id, source_id, "changes")
            mode = "full" if full or not cursor else "incremental"
            bucket = int(current.timestamp()) // max(1, self.settings.SOURCE_SYNC_POLL_INTERVAL_SECONDS)
            key = f"source-sync-scheduler:{source_id}:{mode}:{bucket}"
            if dry_run:
                session.rollback()
                return SourceSyncScheduleResult(tenant_id, source_id, mode, None, False, "dry_run")
            existing = session.scalar(select(ProcessingJobModel).where(ProcessingJobModel.tenant_id == tenant_id, ProcessingJobModel.idempotency_key == key))
            if existing is not None:
                session.rollback()
                return SourceSyncScheduleResult(tenant_id, source_id, mode, existing.id, False, "idempotency_key")
            job = ProcessingRepository(session).create_job(tenant_id=tenant_id, job_type="source_sync", entity_type="external_source", entity_id=source_id, idempotency_key=key, payload={"external_source_id": source_id, "reconciliation": mode == "full"}, priority=5, provider_key=source.source_type, provider_scope="source")
            session.commit()
            self.logger.info("source_sync_job_scheduled", extra={"source_id": source_id, "tenant_id": tenant_id, "mode": mode, "job_id": job.id})
            return SourceSyncScheduleResult(tenant_id, source_id, mode, job.id, True)

    def tick(self, *, now: datetime | None = None) -> tuple[SourceSyncScheduleResult, ...]:
        if not self.enabled:
            return ()
        current = now or datetime.now(timezone.utc)
        with self.session_factory() as session:
            scheduled_sources = session.scalars(
                select(ExternalSourceModel)
                .where(ExternalSourceModel.status == "active")
                .order_by(ExternalSourceModel.tenant_id, ExternalSourceModel.id)
                .limit(self.settings.SOURCE_SYNC_MAX_SOURCES_PER_TICK)
            )
            # Keep this portable across SQLite/PostgreSQL JSON implementations.
            sources = tuple(
                source
                for source in scheduled_sources
                if not is_external_source_decommissioned(source)
            )
        results = []
        for source in sources:
            try:
                results.append(self.enqueue_source(source.tenant_id, source.id, now=current))
            except Exception as exc:
                self.logger.exception("source_sync_source_failed", extra={"source_id": source.id, "tenant_id": source.tenant_id, "mode": "unknown", "error_code": type(exc).__name__})
                results.append(SourceSyncScheduleResult(source.tenant_id, source.id, None, None, False, type(exc).__name__))
        return tuple(results)
