from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VideoMetadataProfileModel(Base):
    __tablename__ = "video_metadata_profiles"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "profile_name", "profile_version",
            name="uq_video_metadata_profiles_tenant_name_version",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_video_metadata_profiles_tenant_id",
        ),
        Index("ix_video_metadata_profiles_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_name: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    optional_json_schema: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    search_config_json: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class VideoAnalysisRunModel(Base):
    __tablename__ = "video_analysis_runs"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "source_asset_id"],
            ["source_assets.tenant_id", "source_assets.id"],
            ondelete="CASCADE",
            name="fk_video_analysis_runs_tenant_source_asset",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "video_metadata_profile_id"],
            ["video_metadata_profiles.tenant_id", "video_metadata_profiles.id"],
            ondelete="RESTRICT",
            name="fk_video_analysis_runs_tenant_profile",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_video_analysis_runs_tenant_id"),
        UniqueConstraint(
            "tenant_id", "idempotency_key",
            name="uq_video_analysis_runs_tenant_idempotency",
        ),
        CheckConstraint(
            "status IN ('pending','preparing','analyzing','completed','failed','cancelled')",
            name="ck_video_analysis_runs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_video_analysis_runs_attempt_count"),
        CheckConstraint("chunk_seconds > 0", name="ck_video_analysis_runs_chunk_seconds"),
        CheckConstraint("total_chunks >= 0", name="ck_video_analysis_runs_total_chunks"),
        CheckConstraint("completed_chunks >= 0", name="ck_video_analysis_runs_completed_chunks"),
        CheckConstraint("completed_chunks <= total_chunks", name="ck_video_analysis_runs_chunk_progress"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_video_analysis_runs_duration"),
        CheckConstraint("source_width IS NULL OR source_width > 0", name="ck_video_analysis_runs_width"),
        CheckConstraint("source_height IS NULL OR source_height > 0", name="ck_video_analysis_runs_height"),
        Index("ix_video_analysis_runs_source_history", "tenant_id", "source_asset_id", "created_at"),
        Index("ix_video_analysis_runs_status_created", "tenant_id", "status", "created_at"),
        Index("ix_video_analysis_runs_fingerprint", "tenant_id", "source_asset_id", "source_fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    video_metadata_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_profile: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_profile_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(100), nullable=False)
    ai_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoAnalysisChunkModel(Base):
    __tablename__ = "video_analysis_chunks"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["video_analysis_runs.tenant_id", "video_analysis_runs.id"],
            ondelete="CASCADE",
            name="fk_video_analysis_chunks_tenant_run",
        ),
        UniqueConstraint("run_id", "chunk_index", name="uq_video_analysis_chunks_run_index"),
        UniqueConstraint("tenant_id", "id", name="uq_video_analysis_chunks_tenant_id"),
        CheckConstraint(
            "status IN ('pending','preparing','uploaded','analyzing','completed','failed')",
            name="ck_video_analysis_chunks_status",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_video_analysis_chunks_index"),
        CheckConstraint("source_start_ms >= 0", name="ck_video_analysis_chunks_start"),
        CheckConstraint("source_end_ms > source_start_ms", name="ck_video_analysis_chunks_range"),
        CheckConstraint("attempt_count >= 0", name="ck_video_analysis_chunks_attempt_count"),
        CheckConstraint(
            "proxy_size_bytes IS NULL OR proxy_size_bytes >= 0",
            name="ck_video_analysis_chunks_proxy_size",
        ),
        Index("ix_video_analysis_chunks_run", "tenant_id", "run_id", "chunk_index"),
        Index("ix_video_analysis_chunks_status", "tenant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    proxy_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provider_file_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    provider_file_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    usage_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    provider_metadata_json: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
