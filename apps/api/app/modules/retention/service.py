from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import Text, cast, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.application_logs.model import ApplicationLogModel
from app.modules.auth_persistence.model import AuthSessionModel, OAuthTransactionModel
from app.modules.external_ingestion.model import (
    AssetIngestionItemModel, AssetIngestionModel,
    ExternalApiCredentialModel, ExternalApiRateLimitModel,
)
from app.modules.processing.model import OutboxEventModel, ProcessingJobModel
from app.modules.retention.model import RetentionCleanupRunModel
from app.modules.search.operations_model import SearchOperationItemModel, SearchOperationRunModel
from app.modules.source_sync.model import SourceSyncRunModel

RECORD_TYPES = (
    "ingestion_urls", "ingestion_payloads", "raw_ai_responses",
    "completed_job_payloads", "dead_letter_details", "expired_oauth_state",
    "expired_sessions", "rate_limit_buckets", "completed_outbox_events",
    "temporary_export_records", "source_sync_runs", "application_logs",
)


class CleanupAlreadyRunning(RuntimeError):
    pass


class RetentionCleanupService:
    """Bounded, resumable redaction/deletion. Asset identity and audit tables are never targets."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory = session_factory
        self.settings = settings

    def create_run(
        self, *, tenant_id: str, record_types: tuple[str, ...] = RECORD_TYPES,
        dry_run: bool = False, max_rows: int | None = None,
        policy_name: str = "default", now: datetime | None = None,
        age_seconds: int | None = None,
    ) -> RetentionCleanupRunModel:
        unknown = set(record_types) - set(RECORD_TYPES)
        if age_seconds is not None and age_seconds < 0:
            raise ValueError("age_seconds cannot be negative")
        if unknown:
            raise ValueError(f"unsupported retention record types: {sorted(unknown)}")
        with self.session_factory() as session:
            existing = session.scalar(select(RetentionCleanupRunModel).where(
                RetentionCleanupRunModel.tenant_id == tenant_id,
                RetentionCleanupRunModel.policy_name == policy_name,
                RetentionCleanupRunModel.status.in_(("pending", "running")),
            ))
            if existing is not None:
                raise CleanupAlreadyRunning(policy_name)
            run = RetentionCleanupRunModel(
                tenant_id=tenant_id, policy_name=policy_name,
                record_types_json=list(record_types), dry_run=dry_run,
                cutoff_at=now or datetime.now(timezone.utc),
                max_rows=max_rows or self.settings.RETENTION_CLEANUP_MAX_ROWS,
                cursor_json=(
                    {"age_seconds": age_seconds}
                    if age_seconds is not None else {}
                ),
                counts_json={},
            )
            session.add(run)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise CleanupAlreadyRunning(policy_name) from exc
            session.refresh(run)
            session.expunge(run)
            return run

    def cancel(self, *, tenant_id: str, run_id: str) -> None:
        with self.session_factory() as session:
            run = session.scalar(select(RetentionCleanupRunModel).where(
                RetentionCleanupRunModel.tenant_id == tenant_id,
                RetentionCleanupRunModel.id == run_id,
            ))
            if run is None:
                raise LookupError(run_id)
            run.cancellation_requested = True
            run.updated_at = datetime.now(timezone.utc)
            session.commit()

    def record_failure(self, *, tenant_id: str, run_id: str, error_code: str) -> None:
        with self.session_factory() as session:
            run = session.scalar(select(RetentionCleanupRunModel).where(
                RetentionCleanupRunModel.tenant_id == tenant_id,
                RetentionCleanupRunModel.id == run_id,
            ))
            if run is None or run.status in {"completed", "cancelled"}:
                return
            now = datetime.now(timezone.utc)
            run.status = "failed"
            run.failed_at = now
            run.updated_at = now
            run.error_json = {
                "code": error_code,
                "message": "Cleanup page failed; sensitive values were omitted.",
            }
            session.commit()

    def execute(self, *, tenant_id: str, run_id: str, cancelled: Callable[[], bool] | None = None) -> RetentionCleanupRunModel:
        processed_this_call = 0
        while processed_this_call < self.settings.RETENTION_CLEANUP_MAX_ROWS:
            with self.session_factory() as session:
                run = session.scalar(select(RetentionCleanupRunModel).where(
                    RetentionCleanupRunModel.tenant_id == tenant_id,
                    RetentionCleanupRunModel.id == run_id,
                ).with_for_update())
                if run is None:
                    raise LookupError(run_id)
                if run.status in {"completed", "cancelled"}:
                    session.expunge(run)
                    return run
                if run.cancellation_requested or (cancelled and cancelled()):
                    run.status = "cancelled"
                    run.completed_at = datetime.now(timezone.utc)
                    session.commit()
                    session.refresh(run); session.expunge(run)
                    return run
                run.status = "running"
                run.failed_at = None
                run.error_json = None
                index = int(run.cursor_json.get("record_type_index", 0))
                if index >= len(run.record_types_json):
                    run.status = "completed"
                    run.completed_at = datetime.now(timezone.utc)
                    run.updated_at = run.completed_at
                    session.commit()
                    session.refresh(run); session.expunge(run)
                    return run
                record_type = run.record_types_json[index]
                limit = min(
                    self.settings.RETENTION_CLEANUP_BATCH_SIZE,
                    run.max_rows - sum(
                        int(v.get("selected", 0)) if isinstance(v, dict) else int(v)
                        for v in run.counts_json.values()
                    ),
                    self.settings.RETENTION_CLEANUP_MAX_ROWS - processed_this_call,
                )
                if limit <= 0:
                    run.status = "completed"
                    run.completed_at = datetime.now(timezone.utc)
                    session.commit(); session.refresh(run); session.expunge(run)
                    return run
                selected, changed = self._process_batch(session, run, record_type, limit)
                counts = dict(run.counts_json)
                previous = counts.get(record_type, {})
                if not isinstance(previous, dict):
                    previous = {"selected": int(previous), "changed": int(previous)}
                counts[record_type] = {
                    "selected": int(previous.get("selected", 0)) + selected,
                    "changed": int(previous.get("changed", 0)) + changed,
                    "mode": "dry_run" if run.dry_run else "applied",
                }
                run.counts_json = counts
                processed_this_call += selected
                if selected < limit or run.dry_run:
                    cursor = dict(run.cursor_json)
                    cursor["record_type_index"] = index + 1
                    run.cursor_json = cursor
                run.checkpoint_version += 1
                run.updated_at = datetime.now(timezone.utc)
                session.commit()
                if selected == 0:
                    continue
        with self.session_factory() as session:
            run = session.get(RetentionCleanupRunModel, run_id)
            session.expunge(run)
            return run

    def _cutoff(self, run: RetentionCleanupRunModel, record_type: str) -> datetime:
        age_override = run.cursor_json.get("age_seconds")
        if age_override is not None:
            return run.cutoff_at - timedelta(seconds=int(age_override))
        value, unit = {
            "ingestion_urls": (self.settings.RETENTION_INGESTION_URL_HOURS, "hours"),
            "ingestion_payloads": (self.settings.RETENTION_COMPLETED_INGESTION_DAYS, "days"),
            "raw_ai_responses": (self.settings.RETENTION_RAW_AI_RESPONSE_DAYS, "days"),
            "completed_job_payloads": (self.settings.RETENTION_COMPLETED_JOB_DAYS, "days"),
            "dead_letter_details": (self.settings.RETENTION_DEAD_LETTER_DAYS, "days"),
            "rate_limit_buckets": (self.settings.RETENTION_RATE_LIMIT_HOURS, "hours"),
            "completed_outbox_events": (self.settings.RETENTION_OUTBOX_DAYS, "days"),
            "temporary_export_records": (self.settings.RETENTION_TEMP_EXPORT_DAYS, "days"),
            "source_sync_runs": (self.settings.RETENTION_SOURCE_SYNC_RUN_DAYS, "days"),
            "application_logs": (0, "hours"),
            "expired_oauth_state": (0, "hours"),
            "expired_sessions": (0, "hours"),
        }[record_type]
        return run.cutoff_at - timedelta(**{unit: value})

    def _process_batch(self, session: Session, run: RetentionCleanupRunModel, record_type: str, limit: int) -> tuple[int, int]:
        cutoff = self._cutoff(run, record_type)
        tenant = run.tenant_id
        ids: list[str] = []

        if record_type == "ingestion_urls":
            rows = list(session.scalars(select(AssetIngestionItemModel).where(
                AssetIngestionItemModel.tenant_id == tenant,
                AssetIngestionItemModel.download_url_redacted_at.is_(None),
                or_(
                    AssetIngestionItemModel.download_url_expires_at <= run.cutoff_at,
                    AssetIngestionItemModel.download_url_consumed_at <= cutoff,
                ),
            ).order_by(AssetIngestionItemModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows:
                    row.download_url = None; row.download_url_ciphertext = None
                    row.download_url_key_version = None; row.download_url_redacted_at = datetime.now(timezone.utc)
        elif record_type == "ingestion_payloads":
            rows = list(session.scalars(select(AssetIngestionModel).where(
                AssetIngestionModel.tenant_id == tenant,
                AssetIngestionModel.completed_at <= cutoff,
                cast(AssetIngestionModel.request_json, Text) != "{}",
            ).order_by(AssetIngestionModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: row.request_json = {}
        elif record_type == "raw_ai_responses":
            rows = list(session.scalars(select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.tenant_id == tenant,
                AssetAiAnalysisModel.completed_at <= cutoff,
                AssetAiAnalysisModel.raw_response_json.is_not(None),
            ).order_by(AssetAiAnalysisModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: row.raw_response_json = None
        elif record_type == "completed_job_payloads":
            rows = list(session.scalars(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant, ProcessingJobModel.status == "completed",
                ProcessingJobModel.completed_at <= cutoff, cast(ProcessingJobModel.payload_json, Text) != "{}",
            ).order_by(ProcessingJobModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: row.payload_json = {}
        elif record_type == "dead_letter_details":
            rows = list(session.scalars(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant, ProcessingJobModel.status == "failed",
                ProcessingJobModel.completed_at <= cutoff,
                ProcessingJobModel.last_error_message.is_not(None),
            ).order_by(ProcessingJobModel.id).limit(limit)))
            if len(rows) < limit:
                rows.extend(list(session.scalars(select(OutboxEventModel).where(
                    OutboxEventModel.tenant_id == tenant,
                    OutboxEventModel.status == "failed",
                    OutboxEventModel.created_at <= cutoff,
                    OutboxEventModel.last_error_message.is_not(None),
                ).order_by(OutboxEventModel.id).limit(limit - len(rows)))))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows:
                    row.payload_json = {}
                    row.last_error_message = "[retained error details redacted]"
        elif record_type == "expired_sessions":
            rows = list(session.scalars(select(AuthSessionModel).where(
                AuthSessionModel.tenant_id == tenant, AuthSessionModel.expires_at <= run.cutoff_at,
            ).order_by(AuthSessionModel.session_id_hash).limit(limit)))
            ids = [row.session_id_hash for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        elif record_type == "expired_oauth_state":
            if tenant != "__platform__":
                return 0, 0
            rows = list(session.scalars(select(OAuthTransactionModel).where(
                OAuthTransactionModel.expires_at <= run.cutoff_at,
            ).order_by(OAuthTransactionModel.state_hash).limit(limit)))
            ids = [row.state_hash for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        elif record_type == "rate_limit_buckets":
            rows = list(session.scalars(select(ExternalApiRateLimitModel).join(
                ExternalApiCredentialModel,
                ExternalApiCredentialModel.id == ExternalApiRateLimitModel.credential_id,
            ).where(
                ExternalApiCredentialModel.tenant_id == tenant,
                ExternalApiRateLimitModel.window_start <= cutoff,
            ).order_by(ExternalApiRateLimitModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        elif record_type == "completed_outbox_events":
            rows = list(session.scalars(select(OutboxEventModel).where(
                OutboxEventModel.tenant_id == tenant,
                OutboxEventModel.status == "published",
                OutboxEventModel.published_at <= cutoff,
            ).order_by(OutboxEventModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        elif record_type == "application_logs":
            rows = list(session.scalars(select(ApplicationLogModel).where(
                ApplicationLogModel.tenant_id == tenant,
                ApplicationLogModel.expires_at <= run.cutoff_at,
            ).order_by(ApplicationLogModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        elif record_type == "source_sync_runs":
            rows = list(session.scalars(select(SourceSyncRunModel).where(
                SourceSyncRunModel.tenant_id == tenant,
                SourceSyncRunModel.status.in_(("completed", "failed", "cancelled")),
                SourceSyncRunModel.updated_at <= cutoff,
            ).order_by(SourceSyncRunModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        elif record_type == "temporary_export_records":
            rows = list(session.scalars(select(SearchOperationItemModel).join(
                SearchOperationRunModel,
                SearchOperationRunModel.id == SearchOperationItemModel.run_id,
            ).where(
                SearchOperationItemModel.tenant_id == tenant,
                SearchOperationRunModel.status.in_(("completed", "failed", "cancelled")),
                SearchOperationRunModel.completed_at <= cutoff,
            ).order_by(SearchOperationItemModel.id).limit(limit)))
            ids = [row.id for row in rows]
            if not run.dry_run:
                for row in rows: session.delete(row)
        return len(ids), 0 if run.dry_run else len(ids)
