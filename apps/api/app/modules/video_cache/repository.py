"""Tenant-scoped persistence and byte accounting for disposable R2 cache metadata."""
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.service import validate_video_mime, video_cache_key


class VideoCacheRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_tenant_and_hash(self, tenant_id: str, content_hash: str) -> VideoCacheObjectModel | None:
        return self.session.scalar(select(VideoCacheObjectModel).where(
            VideoCacheObjectModel.tenant_id == tenant_id,
            VideoCacheObjectModel.content_hash == content_hash,
        ))

    def get_by_id(self, tenant_id: str, record_id: str) -> VideoCacheObjectModel | None:
        return self.session.scalar(select(VideoCacheObjectModel).where(
            VideoCacheObjectModel.tenant_id == tenant_id,
            VideoCacheObjectModel.id == record_id,
        ))

    def create_preparing(
        self, *, tenant_id: str, asset_id: str, source_asset_id: str,
        content_hash: str, mime_type: str, reserved_bytes: int,
    ) -> VideoCacheObjectModel:
        key = video_cache_key(tenant_id, content_hash)
        validate_video_mime(mime_type)
        if reserved_bytes <= 0:
            raise ValueError("Video reservation must be positive")
        asset = self.session.scalar(select(AssetModel).where(
            AssetModel.tenant_id == tenant_id, AssetModel.id == asset_id,
            AssetModel.content_hash == content_hash,
        ))
        source_asset = self.session.scalar(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.id == source_asset_id,
        ))
        if asset is None or source_asset is None or not asset.mime_type or not source_asset.mime_type:
            raise ValueError("Video cache source identity is unavailable")
        link = self.session.scalar(select(AssetSourceLinkModel.id).where(
            AssetSourceLinkModel.tenant_id == tenant_id,
            AssetSourceLinkModel.asset_id == asset_id,
            AssetSourceLinkModel.source_asset_id == source_asset_id,
        ))
        if link is None or not asset.mime_type.startswith("video/") or not source_asset.mime_type.startswith("video/"):
            raise ValueError("Video cache source identity is unavailable")
        existing = self.get_by_tenant_and_hash(tenant_id, content_hash)
        if existing is not None:
            return existing
        record = VideoCacheObjectModel(
            tenant_id=tenant_id, asset_id=asset_id, source_asset_id=source_asset_id,
            content_hash=content_hash, r2_key=key, mime_type=mime_type,
            reserved_bytes=reserved_bytes, status="preparing",
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _require(self, tenant_id: str, record_id: str) -> VideoCacheObjectModel:
        record = self.get_by_id(tenant_id, record_id)
        if record is None:
            raise LookupError("Video cache object is unavailable")
        return record

    def mark_ready(self, tenant_id: str, record_id: str, *, size_bytes: int, etag: str | None) -> None:
        record = self._require(tenant_id, record_id)
        if record.status != "preparing" or size_bytes <= 0:
            raise ValueError("Video cache object cannot become ready")
        record.size_bytes = size_bytes
        record.reserved_bytes = 0
        record.etag = etag
        record.status = "ready"
        record.cached_at = utcnow()
        record.last_accessed_at = record.cached_at
        record.multipart_upload_id = None
        record.updated_at = utcnow()
        record.next_attempt_at = None
        record.last_error_code = None
        record.last_error_message = None
        self.session.flush()

    def _mark_error(
        self, tenant_id: str, record_id: str, *,
        status: str, code: str, next_attempt_at: datetime | None = None,
    ) -> None:
        record = self._require(tenant_id, record_id)
        if not code.isidentifier() or len(code) > 100:
            raise ValueError("Invalid safe error code")
        record.status = status
        record.reserved_bytes = 0
        record.attempt_count += 1
        record.next_attempt_at = next_attempt_at
        record.last_error_code = code
        record.last_error_message = "Video cache operation failed"
        record.updated_at = utcnow()
        self.session.flush()

    def mark_retry(self, tenant_id: str, record_id: str, *, code: str, next_attempt_at: datetime) -> None:
        self._mark_error(tenant_id, record_id, status="retry", code=code, next_attempt_at=next_attempt_at)

    def mark_failed(self, tenant_id: str, record_id: str, *, code: str) -> None:
        self._mark_error(tenant_id, record_id, status="failed", code=code)

    def mark_deleting(self, tenant_id: str, record_id: str) -> None:
        record = self._require(tenant_id, record_id)
        record.status = "deleting"
        record.updated_at = utcnow()
        self.session.flush()

    def delete_record(self, tenant_id: str, record_id: str) -> None:
        self.session.delete(self._require(tenant_id, record_id))
        self.session.flush()

    def total_ready_bytes(self, tenant_id: str | None = None) -> int:
        statement = select(func.coalesce(func.sum(VideoCacheObjectModel.size_bytes), 0)).where(
            VideoCacheObjectModel.status == "ready"
        )
        if tenant_id is not None:
            statement = statement.where(VideoCacheObjectModel.tenant_id == tenant_id)
        return int(self.session.scalar(statement) or 0)

    def total_reserved_bytes(self, tenant_id: str | None = None) -> int:
        statement = select(func.coalesce(func.sum(VideoCacheObjectModel.reserved_bytes), 0)).where(
            VideoCacheObjectModel.status == "preparing"
        )
        if tenant_id is not None:
            statement = statement.where(VideoCacheObjectModel.tenant_id == tenant_id)
        return int(self.session.scalar(statement) or 0)

    def touch_access(self, tenant_id: str, record_id: str, *, debounce_seconds: int = 300) -> bool:
        """Update LRU at most once per debounce window, without a read/write race."""
        now = utcnow()
        result = self.session.execute(
            update(VideoCacheObjectModel).where(
                VideoCacheObjectModel.tenant_id == tenant_id,
                VideoCacheObjectModel.id == record_id,
                VideoCacheObjectModel.status == "ready",
                (VideoCacheObjectModel.last_accessed_at.is_(None))
                | (VideoCacheObjectModel.last_accessed_at <= now - timedelta(seconds=debounce_seconds)),
            ).values(last_accessed_at=now, updated_at=now)
        )
        return bool(result.rowcount)
