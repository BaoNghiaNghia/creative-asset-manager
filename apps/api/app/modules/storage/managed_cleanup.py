from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.processing.types import JobStatus
from app.domain.providers.contracts import AssetStorageProvider, DeleteStoredAssetInput, StorageProviderError
from app.modules.ai_batch.model import AiBatchItemModel, AiBatchJobModel, BATCH_TERMINAL_STATUSES, ITEM_TERMINAL_STATUSES
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.repository import ManagedStorageRepository

logger = logging.getLogger("cam.managed_storage_cleanup")
UTC = timezone.utc
ACTIVE_ANALYSIS_STATES = frozenset({"pending", "running", "budget_blocked"})
ACTIVE_JOB_STATES = frozenset({JobStatus.PENDING.value, JobStatus.PROCESSING.value, JobStatus.RETRY.value})


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ManagedStorageCleanupResult:
    selected: int = 0
    eligible: int = 0
    eligible_completed: int = 0
    eligible_failed: int = 0
    deleted: int = 0
    already_missing: int = 0
    skipped_active: int = 0
    skipped_not_ready: int = 0
    failed: int = 0

    def document(self) -> dict[str, int]:
        return asdict(self)


class ManagedStorageCleanupService:
    """Safely removes only expired Creative AI managed-storage binaries."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        provider: AssetStorageProvider,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.provider = provider

    async def execute(
        self,
        *,
        tenant_id: str | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> ManagedStorageCleanupResult:
        bounded = min(
            max(1, limit or self.settings.MANAGED_STORAGE_CLEANUP_BATCH_SIZE),
            self.settings.MANAGED_STORAGE_CLEANUP_MAX_ITEMS_PER_RUN,
        )
        current = now or datetime.now(UTC)
        with self.session_factory() as session:
            candidate_ids = ManagedStorageRepository(session).list_cleanup_candidate_ids(
                tenant_id=tenant_id, limit=bounded
            )
        result = ManagedStorageCleanupResult(selected=len(candidate_ids))
        for storage_id in candidate_ids:
            outcome = await self._process_one(storage_id, dry_run=dry_run, now=current)
            values = result.document()
            values[outcome] += 1
            if outcome in {"eligible_completed", "eligible_failed"}:
                values["eligible"] += 1
            result = ManagedStorageCleanupResult(**values)
        return result

    async def _process_one(self, storage_id: str, *, dry_run: bool, now: datetime) -> str:
        with self.session_factory() as session:
            try:
                record = ManagedStorageRepository(session).get_for_cleanup(storage_id)
                if record is None or record.status != "stored" or not record.remote_file_id:
                    session.rollback()
                    return "skipped_not_ready"
                decision = self._eligibility(session, record, now)
                if decision not in {"eligible_completed", "eligible_failed"}:
                    session.rollback()
                    return decision
                if dry_run:
                    session.rollback()
                    return decision
                try:
                    await self.provider.delete_asset(
                        DeleteStoredAssetInput(
                            tenant_id=record.tenant_id,
                            asset_id=record.asset_id,
                            remote_file_id=record.remote_file_id,
                        )
                    )
                    remote_missing = False
                except StorageProviderError as exc:
                    if exc.code == "managed_storage_object_missing" or exc.status_code == 404:
                        remote_missing = True
                    else:
                        session.rollback()
                        logger.warning(
                            "managed_storage_cleanup_failed",
                            extra={
                                "tenant_id": record.tenant_id,
                                "asset_id": record.asset_id,
                                "storage_provider": record.storage_provider,
                                "error_code": exc.code,
                                "retryable": exc.retryable,
                            },
                        )
                        return "failed"
                # The record is still locked; eligibility was checked directly before remote delete.
                ManagedStorageRepository(session).delete_record(record)
                session.commit()
                logger.info(
                    "managed_storage_cleanup_remote_missing" if remote_missing else "managed_storage_cleanup_deleted",
                    extra={
                        "tenant_id": record.tenant_id,
                        "asset_id": record.asset_id,
                        "storage_provider": record.storage_provider,
                        "reason": decision,
                    },
                )
                return "already_missing" if remote_missing else "deleted"
            except Exception:
                session.rollback()
                logger.exception("managed_storage_cleanup_failed", extra={"storage_id": storage_id})
                return "failed"

    def _eligibility(
        self, session: Session, record: AssetStorageObjectModel, now: datetime
    ) -> str:
        analyses = tuple(session.scalars(select(AssetAiAnalysisModel).where(
            AssetAiAnalysisModel.tenant_id == record.tenant_id,
            AssetAiAnalysisModel.asset_id == record.asset_id,
            AssetAiAnalysisModel.content_hash == record.content_hash,
        )))
        if any(
            analysis.status in ACTIVE_ANALYSIS_STATES
            or (analysis.status == "failed" and bool(analysis.failure_retryable))
            for analysis in analyses
        ):
            return "skipped_active"
        analysis_ids = tuple(analysis.id for analysis in analyses)
        if self._has_active_jobs(session, record, analysis_ids):
            return "skipped_active"
        if self._has_active_batch(session, record, analysis_ids):
            return "skipped_active"
        if record.stored_at is None:
            return "skipped_not_ready"
        # A completion from before this physical staging upload cannot qualify it.
        relevant = tuple(
            analysis for analysis in analyses
            if analysis.completed_at is not None and _utc(analysis.completed_at) >= _utc(record.stored_at)
        )
        completed = tuple(analysis for analysis in relevant if analysis.status == "completed")
        if completed and max(_utc(value.completed_at) for value in completed) <= now - timedelta(
            hours=self.settings.MANAGED_STORAGE_COMPLETED_RETENTION_HOURS
        ):
            return "eligible_completed"
        failed = tuple(
            analysis for analysis in relevant
            if analysis.status == "failed" and not bool(analysis.failure_retryable)
        )
        if failed and max(_utc(value.completed_at) for value in failed) <= now - timedelta(
            hours=self.settings.MANAGED_STORAGE_FAILED_RETENTION_HOURS
        ):
            return "eligible_failed"
        return "skipped_not_ready"

    @staticmethod
    def _has_active_jobs(
        session: Session,
        record: AssetStorageObjectModel,
        analysis_ids: tuple[str, ...],
    ) -> bool:
        identities = (record.asset_id, *analysis_ids)
        return session.scalar(select(ProcessingJobModel.id).where(
            ProcessingJobModel.tenant_id == record.tenant_id,
            ProcessingJobModel.entity_id.in_(identities),
            ProcessingJobModel.status.in_(ACTIVE_JOB_STATES),
        ).limit(1)) is not None

    @staticmethod
    def _has_active_batch(
        session: Session,
        record: AssetStorageObjectModel,
        analysis_ids: tuple[str, ...],
    ) -> bool:
        if not analysis_ids:
            return False
        return session.scalar(
            select(AiBatchItemModel.id)
            .join(AiBatchJobModel, AiBatchJobModel.id == AiBatchItemModel.batch_job_id)
            .where(
                AiBatchItemModel.tenant_id == record.tenant_id,
                AiBatchItemModel.analysis_id.in_(analysis_ids),
                (
                    ~AiBatchItemModel.status.in_(ITEM_TERMINAL_STATUSES)
                    | ~AiBatchJobModel.status.in_(BATCH_TERMINAL_STATUSES)
                ),
            ).limit(1)
        ) is not None
