from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ingestion_id() -> str:
    return f"ing_{uuid4().hex}"


def ingestion_item_id() -> str:
    return f"ingi_{uuid4().hex}"


class ExternalApiCredentialModel(Base):
    __tablename__ = "external_api_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_external_api_credentials_tenant_source",
        ),
        UniqueConstraint("secret_hash", name="uq_external_api_credentials_secret_hash"),
        UniqueConstraint("tenant_id", "id", name="uq_external_api_credentials_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "external_source_id",
            name="uq_external_api_credentials_tenant_id_source",
        ),
        CheckConstraint(
            "rate_limit_per_minute > 0",
            name="ck_external_api_credentials_rate_limit",
        ),
        Index(
            "ix_external_api_credentials_source",
            "tenant_id",
            "external_source_id",
            "active",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExternalApiRateLimitModel(Base):
    __tablename__ = "external_api_rate_limits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_id"],
            ["external_api_credentials.id"],
            ondelete="CASCADE",
            name="fk_external_api_rate_limits_credential",
        ),
        UniqueConstraint(
            "credential_id",
            "window_start",
            name="uq_external_api_rate_limits_window",
        ),
        CheckConstraint("request_count > 0", name="ck_external_api_rate_limits_count"),
        Index("ix_external_api_rate_limits_window", "window_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    credential_id: Mapped[str] = mapped_column(String(36), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AssetIngestionModel(Base):
    __tablename__ = "asset_ingestions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="RESTRICT",
            name="fk_asset_ingestions_tenant_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id", "external_source_id"],
            ["external_api_credentials.tenant_id", "external_api_credentials.id", "external_api_credentials.external_source_id"],
            ondelete="RESTRICT",
            name="fk_asset_ingestions_tenant_credential",
        ),
        UniqueConstraint(
            "tenant_id",
            "external_source_id",
            "idempotency_key",
            name="uq_asset_ingestions_tenant_source_key",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_asset_ingestions_tenant_id"),
        CheckConstraint(
            "status IN ('accepted', 'processing', 'completed', 'partial_failed', 'failed')",
            name="ck_asset_ingestions_status",
        ),
        CheckConstraint("received_count > 0", name="ck_asset_ingestions_received_count"),
        Index("ix_asset_ingestions_source_created", "tenant_id", "external_source_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ingestion_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    credential_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="accepted")
    received_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssetIngestionItemModel(Base):
    __tablename__ = "asset_ingestion_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "ingestion_id"],
            ["asset_ingestions.tenant_id", "asset_ingestions.id"],
            ondelete="CASCADE",
            name="fk_asset_ingestion_items_tenant_ingestion",
        ),
        UniqueConstraint("ingestion_id", "position", name="uq_asset_ingestion_items_position"),
        ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["source_assets.tenant_id", "source_assets.id"],
            ondelete="RESTRICT",
            name="fk_asset_ingestion_items_tenant_source_asset",
        ),
        ForeignKeyConstraint(
            ["processing_job_id"],
            ["processing_jobs.id"],
            ondelete="SET NULL",
            name="fk_asset_ingestion_items_processing_job",
        ),
        UniqueConstraint(
            "ingestion_id",
            "external_asset_id",
            name="uq_asset_ingestion_items_external_id",
        ),
        CheckConstraint("position >= 0", name="ck_asset_ingestion_items_position"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_asset_ingestion_items_status",
        ),
        Index("ix_asset_ingestion_items_status", "tenant_id", "ingestion_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=ingestion_item_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ingestion_id: Mapped[str] = mapped_column(String(36), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    external_asset_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    download_url: Mapped[str | None] = mapped_column(Text)
    download_url_ciphertext: Mapped[str | None] = mapped_column(Text)
    download_url_key_version: Mapped[str | None] = mapped_column(String(64))
    download_url_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_url_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_url_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_checksum: Mapped[str | None] = mapped_column(String(255))
    filename: Mapped[str | None] = mapped_column(String(1024))
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    source_asset_id: Mapped[str | None] = mapped_column(String(36))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
