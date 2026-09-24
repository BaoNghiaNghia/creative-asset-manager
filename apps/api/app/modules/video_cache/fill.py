"""Application admission for durable, idempotent original-video cache fills."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.assets.media_types import infer_media_type
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.metrics import emit_counter
from app.modules.video_cache.quota import VideoCacheQuota
from app.modules.video_cache.repository import VideoCacheRepository
from app.modules.video_cache.service import video_cache_key
from app.providers.cloudflare.r2 import R2Adapter


class CacheFillStatus(str, Enum):
    READY = "ready"
    ENQUEUED = "enqueued"
    ALREADY_PREPARING = "already_preparing"
    BYPASSED_NOT_VIDEO = "bypassed_not_video"
    BYPASSED_OVERSIZED = "bypassed_oversized"
    BYPASSED_NO_HASH = "bypassed_no_hash"
    BYPASSED_NO_SIZE = "bypassed_no_size"
    BYPASSED_QUOTA = "bypassed_quota"
    BYPASSED_SOURCE_CHANGED = "bypassed_source_changed"
    BYPASSED_FAILED = "bypassed_failed"
    DISABLED = "disabled"


@dataclass(frozen=True)
class CacheFillResult:
    status: CacheFillStatus
    record_id: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.status.value.startswith("bypassed_"):
            emit_counter("video_cache_bypass_total")


_ACTIVE_JOBS = frozenset(("pending", "processing", "retry"))


class VideoCacheFillService:
    def __init__(self, session_factory: Callable[[], Session], settings: Settings, provider: R2Adapter | None):
        self.session_factory = session_factory
        self.settings = settings
        self.provider = provider

    async def ensure_video_cache_fill(
        self, *, tenant_id: str, asset_id: str, source_asset_id: str,
        content_hash: str | None, retry_failed: bool = False,
    ) -> CacheFillResult:
        if not self.settings.R2_VIDEO_CACHE_ENABLED or self.provider is None:
            return CacheFillResult(CacheFillStatus.DISABLED)
        try:
            video_cache_key(tenant_id, content_hash or "")
        except ValueError:
            return CacheFillResult(CacheFillStatus.BYPASSED_NO_HASH)
        quota = VideoCacheQuota(self.session_factory, self.settings, self.provider)
        # Recheck admission after every out-of-transaction physical deletion.
        for _ in range(self.settings.R2_VIDEO_CACHE_CLEANUP_MAX_ITEMS_PER_RUN + 1):
            pressure = False
            required = 0
            with quota.transaction() as session:
                asset = session.scalar(select(AssetModel).where(
                    AssetModel.tenant_id == tenant_id, AssetModel.id == asset_id,
                ))
                source = session.scalar(select(SourceAssetModel).where(
                    SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.id == source_asset_id,
                    SourceAssetModel.deleted_at.is_(None),
                ))
                link = session.scalar(select(AssetSourceLinkModel.id).where(
                    AssetSourceLinkModel.tenant_id == tenant_id,
                    AssetSourceLinkModel.asset_id == asset_id,
                    AssetSourceLinkModel.source_asset_id == source_asset_id,
                ))
                if asset is None or source is None or link is None or asset.content_hash != content_hash:
                    return CacheFillResult(CacheFillStatus.BYPASSED_SOURCE_CHANGED)
                resolved_mime_type = infer_media_type(
                    source.filename, source.mime_type, asset.mime_type
                )
                if not resolved_mime_type.startswith("video/"):
                    return CacheFillResult(CacheFillStatus.BYPASSED_NOT_VIDEO)
                if asset.size_bytes is None or source.size_bytes is None or asset.size_bytes <= 0 or source.size_bytes <= 0:
                    return CacheFillResult(CacheFillStatus.BYPASSED_NO_SIZE)
                if asset.size_bytes != source.size_bytes:
                    return CacheFillResult(CacheFillStatus.BYPASSED_SOURCE_CHANGED)
                required = int(asset.size_bytes)
                if required > self.settings.R2_VIDEO_CACHE_MAX_OBJECT_BYTES:
                    return CacheFillResult(CacheFillStatus.BYPASSED_OVERSIZED)
                repo = VideoCacheRepository(session)
                row = repo.get_by_tenant_and_hash(tenant_id, content_hash)
                if row is not None:
                    if row.status == "ready":
                        return CacheFillResult(CacheFillStatus.READY, row.id)
                    if row.status == "deleting":
                        return CacheFillResult(CacheFillStatus.BYPASSED_QUOTA)
                    job = session.scalar(select(ProcessingJobModel).where(
                        ProcessingJobModel.tenant_id == tenant_id,
                        ProcessingJobModel.id == row.fill_job_id,
                    )) if row.fill_job_id else None
                    if job is not None and job.status in _ACTIVE_JOBS:
                        return CacheFillResult(CacheFillStatus.ALREADY_PREPARING, row.id, job.id)
                    if row.status == "preparing":
                        return CacheFillResult(CacheFillStatus.ALREADY_PREPARING, row.id, row.fill_job_id)
                    if row.status == "failed" and not retry_failed:
                        return CacheFillResult(CacheFillStatus.BYPASSED_FAILED, row.id)
                if not quota.can_reserve(session, required):
                    pressure = True
                else:
                    if row is None:
                        row = repo.create_preparing(
                            tenant_id=tenant_id, asset_id=asset_id, source_asset_id=source_asset_id,
                            content_hash=content_hash, mime_type=resolved_mime_type,
                            reserved_bytes=required,
                        )
                    else:
                        row.asset_id = asset_id
                        row.source_asset_id = source_asset_id
                        row.mime_type = resolved_mime_type
                        row.multipart_upload_id = None
                        row.size_bytes = 0
                        row.status = "preparing"
                        row.reserved_bytes = required
                        row.fill_generation += 1
                        row.next_attempt_at = None
                        row.last_error_code = None
                        row.last_error_message = None
                        row.updated_at = utcnow()
                    payload = {
                        "tenant_id": tenant_id, "asset_id": asset_id,
                        "source_asset_id": source_asset_id, "content_hash": content_hash,
                    }
                    job = ProcessingRepository(session, self.settings).create_job(
                        tenant_id=tenant_id, job_type="video_cache_fill",
                        entity_type="video_cache_object", entity_id=row.id,
                        idempotency_key=f"video-cache-fill:{content_hash}:{row.fill_generation}",
                        payload=payload, max_attempts=5,
                    )
                    row.fill_job_id = job.id
                    session.flush()
                    return CacheFillResult(CacheFillStatus.ENQUEUED, row.id, job.id)
            if not pressure:
                return CacheFillResult(CacheFillStatus.BYPASSED_QUOTA)
            if not await quota.reclaim_for(required):
                return CacheFillResult(CacheFillStatus.BYPASSED_QUOTA)
        return CacheFillResult(CacheFillStatus.BYPASSED_QUOTA)
