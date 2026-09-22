from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PublicShareModel(Base):
    __tablename__ = "public_shares"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_public_shares_public_id"),
        UniqueConstraint("tenant_id", "id", name="uq_public_shares_tenant_id"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_public_shares_status"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 255", name="ck_public_shares_name"),
        CheckConstraint("length(secret_digest) = 64", name="ck_public_shares_secret_digest"),
        Index("ix_public_shares_tenant_status", "tenant_id", "status", "updated_at"),
        Index("ix_public_shares_secret_digest", "secret_digest"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    secret_key_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    allow_comments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def is_active_at(self, now: datetime | None = None) -> bool:
        instant = aware_utc(now or utcnow())
        expires_at = aware_utc(self.expires_at) if self.expires_at is not None else None
        return self.status == "active" and self.revoked_at is None and (expires_at is None or expires_at > instant)


class PublicShareScopeModel(Base):
    __tablename__ = "public_share_scopes"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_public_share_scopes_tenant_share"),
        ForeignKeyConstraint(["tenant_id", "external_source_id"], ["external_sources.tenant_id", "external_sources.id"], ondelete="RESTRICT", name="fk_public_share_scopes_tenant_source"),
        UniqueConstraint("share_id", "external_source_id", "folder_external_id", name="uq_public_share_scopes_identity"),
        Index("ix_public_share_scopes_tenant_share", "tenant_id", "share_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    share_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    folder_external_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    folder_name: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class PublicShareGuestModel(Base):
    __tablename__ = "public_share_guests"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_public_share_guests_tenant_share"),
        UniqueConstraint("tenant_id", "share_id", "id", name="uq_public_share_guests_tenant_share_id"),
        CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 160", name="ck_public_share_guests_display_name"),
        Index("ix_public_share_guests_tenant_share", "tenant_id", "share_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    share_id: Mapped[str] = mapped_column(String(36), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PublicShareSessionModel(Base):
    __tablename__ = "public_share_sessions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_public_share_sessions_tenant_share"),
        ForeignKeyConstraint(["tenant_id", "share_id", "guest_id"], ["public_share_guests.tenant_id", "public_share_guests.share_id", "public_share_guests.id"], ondelete="RESTRICT", name="fk_public_share_sessions_tenant_share_guest"),
        UniqueConstraint("session_digest", name="uq_public_share_sessions_digest"),
        UniqueConstraint("tenant_id", "share_id", "id", name="uq_public_share_sessions_tenant_share_id"),
        CheckConstraint("length(session_digest) = 64", name="ck_public_share_sessions_digest"),
        Index("ix_public_share_sessions_tenant_share", "tenant_id", "share_id", "expires_at"),
        Index("ix_public_share_sessions_active", "expires_at", "revoked_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    share_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    guest_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def is_active_at(self, now: datetime | None = None) -> bool:
        instant = aware_utc(now or utcnow())
        return self.revoked_at is None and aware_utc(self.expires_at) > instant


class AssetAnnotationModel(Base):
    __tablename__ = "asset_annotations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_asset_annotations_tenant_share"),
        ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_asset"),
        ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["source_assets.tenant_id", "source_assets.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_source_asset"),
        ForeignKeyConstraint(["tenant_id", "share_id", "guest_id"], ["public_share_guests.tenant_id", "public_share_guests.share_id", "public_share_guests.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_share_guest"),
        ForeignKeyConstraint(["tenant_id", "share_id", "parent_annotation_id"], ["asset_annotations.tenant_id", "asset_annotations.share_id", "asset_annotations.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_share_parent"),
        UniqueConstraint("tenant_id", "share_id", "id", name="uq_asset_annotations_tenant_share_id"),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_asset_annotations_status"),
        CheckConstraint("(anchor_x IS NULL AND anchor_y IS NULL) OR (anchor_x >= 0 AND anchor_x <= 1 AND anchor_y >= 0 AND anchor_y <= 1)", name="ck_asset_annotations_anchor_pair"),
        CheckConstraint("length(plain_text) <= 10000", name="ck_asset_annotations_plain_text"),
        Index("ix_asset_annotations_tenant_share_asset", "tenant_id", "share_id", "asset_id", "source_asset_id", "created_at"),
        Index("ix_asset_annotations_tenant_share_status", "tenant_id", "share_id", "status", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    share_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    guest_id: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_annotation_id: Mapped[str | None] = mapped_column(String(36))
    anchor_x: Mapped[float | None] = mapped_column(Float)
    anchor_y: Mapped[float | None] = mapped_column(Float)
    content_json: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    plain_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(512))

class PublicReviewRateLimitModel(Base):
    """Shared fixed-window counters. Client identity is SHA-256 digested."""
    __tablename__ = "public_review_rate_limits"
    __table_args__ = (
        UniqueConstraint("operation", "client_digest", "window_start", name="uq_public_review_rate_limits_window"),
        CheckConstraint("request_count > 0", name="ck_public_review_rate_limits_count"),
        Index("ix_public_review_rate_limits_window", "window_start"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    client_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(nullable=False, default=1)
