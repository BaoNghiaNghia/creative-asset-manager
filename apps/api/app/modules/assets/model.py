from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    JSON,
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExternalSourceModel(Base):
    __tablename__ = "external_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_key", name="uq_external_sources_tenant_key"),
        UniqueConstraint("tenant_id", "id", name="uq_external_sources_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "oauth_connection_id"],
            ["oauth_connections.tenant_id", "oauth_connections.id"],
            ondelete="SET NULL",
            name="fk_external_sources_tenant_oauth_connection",
        ),
        CheckConstraint(
            "status IN ('active','reconnect_required','disconnected')",
            name="ck_external_sources_status",
        ),
        Index("ix_external_sources_tenant_type", "tenant_id", "source_type"),
        Index("ix_external_sources_tenant_connection", "tenant_id", "oauth_connection_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    oauth_connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class SourceAssetModel(Base):
    __tablename__ = "source_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_source_assets_tenant_source",
        ),
        UniqueConstraint(
            "tenant_id",
            "external_source_id",
            "external_asset_id",
            name="uq_source_assets_source_identity",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_source_assets_tenant_id"),
        Index("ix_source_assets_tenant_deleted", "tenant_id", "deleted_at"),
        Index(
            "ix_source_assets_reconciliation_generation",
            "tenant_id", "external_source_id", "last_seen_generation", "deleted_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_asset_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_checksum: Mapped[str | None] = mapped_column(String(255))
    provider_version: Mapped[str | None] = mapped_column(String(255))
    hashed_provider_checksum: Mapped[str | None] = mapped_column(String(255))
    hashed_provider_version: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_generation: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AssetModel(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "content_hash", name="uq_assets_tenant_content_hash"),
        UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id"),
        Index("ix_assets_content_hash", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_image_hash: Mapped[str | None] = mapped_column(String(64))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AssetSourceLinkModel(Base):
    __tablename__ = "asset_source_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE",
            name="fk_asset_source_links_tenant_asset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["source_assets.tenant_id", "source_assets.id"],
            ondelete="CASCADE",
            name="fk_asset_source_links_tenant_source_asset",
        ),
        UniqueConstraint("asset_id", "source_asset_id", name="uq_asset_source_links_pair"),
        Index("ix_asset_source_links_source_asset", "source_asset_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SourceSyncCursorModel(Base):
    __tablename__ = "source_sync_cursors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_source_sync_cursors_tenant_source",
        ),
        UniqueConstraint(
            "tenant_id",
            "external_source_id",
            "cursor_key",
            name="uq_source_sync_cursors_source_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cursor_key: Mapped[str] = mapped_column(String(100), nullable=False, default="changes")
    cursor_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
