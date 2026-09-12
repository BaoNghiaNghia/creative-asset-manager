from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.redaction import redact_url_queries
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.assets.model import AssetModel


class StagingCapacityExceeded(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ManagedStorageRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(
        self, *, tenant_id: str, asset_id: str, content_hash: str, storage_provider: str
    ) -> AssetStorageObjectModel:
        existing = self.get(tenant_id, asset_id, storage_provider)
        if existing is not None:
            if existing.content_hash != content_hash:
                raise ValueError("managed storage content hash does not match the asset")
            return existing
        try:
            with self.session.begin_nested():
                record = AssetStorageObjectModel(
                    tenant_id=tenant_id,
                    asset_id=asset_id,
                    content_hash=content_hash,
                    storage_provider=storage_provider,
                )
                self.session.add(record)
                self.session.flush()
            return record
        except IntegrityError:
            existing = self.get(tenant_id, asset_id, storage_provider)
            if existing is None:
                raise
            return existing

    def get(
        self, tenant_id: str, asset_id: str, storage_provider: str
    ) -> AssetStorageObjectModel | None:
        return self.session.scalar(
            select(AssetStorageObjectModel).where(
                AssetStorageObjectModel.tenant_id == tenant_id,
                AssetStorageObjectModel.asset_id == asset_id,
                AssetStorageObjectModel.storage_provider == storage_provider,
            )
        )

    def reserve_staging_upload(
        self, record: AssetStorageObjectModel, *, remote_folder_id: str,
        bytes_required: int, maximum_bytes: int,
    ) -> None:
        """Atomically reserve byte capacity before a managed-storage upload."""
        if maximum_bytes <= 0:
            self.mark_uploading(record, remote_folder_id=remote_folder_id)
            return
        required = max(0, int(bytes_required))
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                "key": f"managed-storage-staging:{remote_folder_id}",
            })
        used = self._staging_bytes_used(
            record.storage_provider, remote_folder_id, exclude_storage_id=record.id,
        )
        if required > maximum_bytes or used + required > maximum_bytes:
            raise StagingCapacityExceeded(
                f"managed storage staging has {used} bytes reserved; "
                f"{required} bytes would exceed {maximum_bytes}"
            )
        self.mark_uploading(record, remote_folder_id=remote_folder_id)

    def _staging_bytes_used(
        self, storage_provider: str, remote_folder_id: str, *,
        exclude_storage_id: str | None = None,
    ) -> int:
        statement = select(func.coalesce(func.sum(AssetModel.size_bytes), 0)).select_from(
            AssetStorageObjectModel
        ).join(AssetModel, and_(
            AssetModel.tenant_id == AssetStorageObjectModel.tenant_id,
            AssetModel.id == AssetStorageObjectModel.asset_id,
        )).where(
            AssetStorageObjectModel.storage_provider == storage_provider,
            AssetStorageObjectModel.remote_folder_id == remote_folder_id,
            AssetStorageObjectModel.status.in_(("stored", "uploading", "retry")),
        )
        if exclude_storage_id:
            statement = statement.where(AssetStorageObjectModel.id != exclude_storage_id)
        return int(self.session.scalar(statement) or 0)

    def staging_bytes_used(self, *, remote_folder_id: str) -> int:
        """Return tracked live bytes in one managed staging folder.

        This deliberately counts only CAM-owned records. Cleanup must never
        infer ownership from arbitrary files found in a Drive folder.
        """
        statement = select(func.coalesce(func.sum(AssetModel.size_bytes), 0)).select_from(
            AssetStorageObjectModel
        ).join(AssetModel, and_(
            AssetModel.tenant_id == AssetStorageObjectModel.tenant_id,
            AssetModel.id == AssetStorageObjectModel.asset_id,
        )).where(
            AssetStorageObjectModel.remote_folder_id == remote_folder_id,
            AssetStorageObjectModel.status.in_(("stored", "uploading", "retry")),
        )
        return int(self.session.scalar(statement) or 0)

    def mark_uploading(
        self, record: AssetStorageObjectModel, *, remote_folder_id: str | None = None,
    ) -> None:
        record.status = "uploading"
        if remote_folder_id:
            record.remote_folder_id = remote_folder_id
        record.attempt_count += 1
        record.next_attempt_at = None
        record.updated_at = utcnow()
        self.session.flush()

    def mark_stored(
        self,
        record: AssetStorageObjectModel,
        *,
        remote_file_id: str,
        remote_folder_id: str,
        web_url: str | None,
    ) -> None:
        now = utcnow()
        record.status = "stored"
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
        record: AssetStorageObjectModel,
        *,
        retryable: bool,
        error_code: str,
        error_message: str,
        max_attempts: int,
    ) -> None:
        now = utcnow()
        record.last_error_code = error_code[:100]
        record.last_error_message = redact_url_queries(error_message)
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


    def list_cleanup_candidate_ids(
        self, *, tenant_id: str | None, remote_folder_id: str, limit: int
    ) -> tuple[str, ...]:
        statement = select(AssetStorageObjectModel.id).where(
            AssetStorageObjectModel.status == "stored",
            AssetStorageObjectModel.remote_file_id.is_not(None),
            AssetStorageObjectModel.remote_folder_id == remote_folder_id,
        )
        if tenant_id is not None:
            statement = statement.where(AssetStorageObjectModel.tenant_id == tenant_id)
        return tuple(self.session.scalars(
            statement.order_by(
                AssetStorageObjectModel.updated_at,
                AssetStorageObjectModel.stored_at,
                AssetStorageObjectModel.id,
            ).limit(limit)
        ))

    def get_for_cleanup(self, storage_id: str) -> AssetStorageObjectModel | None:
        statement = select(AssetStorageObjectModel).where(
            AssetStorageObjectModel.id == storage_id
        )
        if self.session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        return self.session.scalar(statement)

    def delete_record(self, record: AssetStorageObjectModel) -> None:
        self.session.delete(record)
        self.session.flush()
