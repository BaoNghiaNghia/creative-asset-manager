"""Durable metadata for private, disposable original-video cache objects."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VideoCacheObjectModel(Base):
    __tablename__ = "video_cache_objects"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_video_cache_tenant"),
        UniqueConstraint("tenant_id", "content_hash", name="uq_video_cache_tenant_hash"),
        UniqueConstraint("r2_key", name="uq_video_cache_r2_key"),
        CheckConstraint("status IN ('preparing','ready','deleting','retry','failed')", name="ck_video_cache_status"),
        CheckConstraint("size_bytes >= 0", name="ck_video_cache_size"),
        CheckConstraint("reserved_bytes >= 0", name="ck_video_cache_reserved"),
        CheckConstraint("attempt_count >= 0", name="ck_video_cache_attempts"),
        CheckConstraint("mime_type LIKE 'video/%'", name="ck_video_cache_video_mime"),
        Index("ix_video_cache_status", "status"),
        Index("ix_video_cache_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_video_cache_last_accessed", "last_accessed_at"),
        Index("ix_video_cache_tenant_status", "tenant_id", "status"),
        Index("ix_video_cache_lru", "status", "last_accessed_at", "cached_at", "id"),
        Index("ix_video_cache_cleanup_due", "status", "next_attempt_at", "cleanup_lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    r2_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    etag: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="preparing")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    fill_job_id: Mapped[str | None] = mapped_column(String(36))
    fill_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # S3-compatible providers treat multipart upload IDs as opaque values and
    # do not guarantee that they fit in a short varchar (R2 currently emits
    # values longer than 255 characters).
    multipart_upload_id: Mapped[str | None] = mapped_column(Text)
    cleanup_claimed_by: Mapped[str | None] = mapped_column(String(255))
    cleanup_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


VIDEO_CDN_DELIVERY_SETTING_KEY = "VIDEO_CDN_DELIVERY_ENABLED"


class VideoDeliveryRuntimeSettingModel(Base):
    """Singleton global runtime intent for Phase 4A CDN delivery rollout."""

    __tablename__ = "video_delivery_runtime_settings"
    __table_args__ = (
        CheckConstraint(
            "setting_key = 'VIDEO_CDN_DELIVERY_ENABLED'",
            name="ck_video_delivery_runtime_setting_key",
        ),
    )

    setting_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    update_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
