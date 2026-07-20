from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.storage.model import MetadataSidecarExportModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MetadataSidecarRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(
        self,
        *,
        tenant_id: str,
        asset_id: str,
        analysis_id: str,
        storage_provider: str,
        document_hash: str,
    ) -> MetadataSidecarExportModel:
        existing = self.get(analysis_id, storage_provider)
        if existing is not None:
            if existing.tenant_id != tenant_id or existing.asset_id != asset_id:
                raise ValueError("sidecar identity does not match analysis")
            if existing.document_hash != document_hash:
                existing.document_hash = document_hash
                if existing.status == "stored":
                    existing.status = "pending"
                self.session.flush()
            return existing
        try:
            with self.session.begin_nested():
                record = MetadataSidecarExportModel(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    analysis_id=analysis_id,
                    storage_provider=storage_provider,
                    document_hash=document_hash,
                )
                self.session.add(record)
                self.session.flush()
            return record
        except IntegrityError:
            existing = self.get(analysis_id, storage_provider)
            if existing is None:
                raise
            return existing

    def get(
        self,
        analysis_id: str,
        storage_provider: str,
    ) -> MetadataSidecarExportModel | None:
        return self.session.scalar(
            select(MetadataSidecarExportModel).where(
                MetadataSidecarExportModel.analysis_id == analysis_id,
                MetadataSidecarExportModel.storage_provider == storage_provider,
            )
        )

    def mark_exporting(self, record: MetadataSidecarExportModel) -> None:
        record.status = "exporting"
        record.attempt_count += 1
        record.next_attempt_at = None
        record.updated_at = utcnow()
        self.session.flush()

    def mark_stored(
        self,
        record: MetadataSidecarExportModel,
        *,
        storage_key: str,
        remote_file_id: str,
        remote_folder_id: str | None,
        web_url: str | None,
    ) -> None:
        now = utcnow()
        record.status = "stored"
        record.storage_key = storage_key
        record.remote_file_id = remote_file_id
        record.remote_folder_id = remote_folder_id
        record.web_url = web_url
        record.last_error_code = None
        record.last_error_message = None
        record.next_attempt_at = None
        record.stored_at = now
        record.updated_at = now
        self.session.flush()

    def mark_failure(
        self,
        record: MetadataSidecarExportModel,
        *,
        retryable: bool,
        error_code: str,
        error_message: str,
        max_attempts: int,
    ) -> None:
        now = utcnow()
        record.last_error_code = error_code[:100]
        record.last_error_message = error_message
        if retryable and record.attempt_count < max_attempts:
            record.status = "retry"
            record.next_attempt_at = now + timedelta(
                seconds=min(5 * (2 ** max(record.attempt_count - 1, 0)), 3600)
            )
        else:
            record.status = "failed"
            record.next_attempt_at = None
        record.updated_at = now
        self.session.flush()
