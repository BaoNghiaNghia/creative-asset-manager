from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MetadataProfileModel(Base):
    __tablename__ = "metadata_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "profile_name", "profile_version",
            name="uq_metadata_profiles_tenant_name_version",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_metadata_profiles_tenant_id"),
        Index("ix_metadata_profiles_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_name: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    optional_json_schema: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    search_config_json: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class AssetAiAnalysisModel(Base):
    __tablename__ = "asset_ai_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE",
            name="fk_asset_ai_analyses_tenant_asset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metadata_profile_id"],
            ["metadata_profiles.tenant_id", "metadata_profiles.id"],
            ondelete="RESTRICT",
            name="fk_asset_ai_analyses_tenant_profile",
        ),
        UniqueConstraint(
            "tenant_id", "asset_id", "metadata_profile_id", "id",
            name="uq_asset_ai_analyses_active_reference",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'budget_blocked')",
            name="ck_asset_ai_analyses_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_asset_ai_analyses_attempt_count"),
        Index(
            "uq_asset_ai_analyses_normal_run",
            "tenant_id",
            "asset_id",
            "content_hash",
            "metadata_profile_id",
            "prompt_version",
            "pipeline_version",
            "ai_provider",
            "ai_model",
            unique=True,
            postgresql_where=text("forced = false"),
            sqlite_where=text("forced = 0"),
        ),
        Index("ix_asset_ai_analyses_history", "tenant_id", "asset_id", "created_at"),
        Index("ix_asset_ai_analyses_status", "status", "created_at"),
        Index("ix_asset_ai_analyses_claim", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_profile: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_profile_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(100), nullable=False)
    ai_provider: Mapped[str | None] = mapped_column(String(100))
    ai_model: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    processing_stage: Mapped[str | None] = mapped_column(String(40))
    claimed_by: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_response_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    metadata_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    search_projection: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    search_projection_version: Mapped[str | None] = mapped_column(String(100))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projection_checksum: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    usage_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    provider_metadata_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT)
    validation_errors_json: Mapped[list | None] = mapped_column(JSON_DOCUMENT)
    failure_retryable: Mapped[bool | None] = mapped_column(Boolean)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
