from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImageGenerationRunModel(Base):
    __tablename__ = "image_generation_runs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["assets.tenant_id", "assets.id"], ondelete="RESTRICT", name="fk_image_generation_runs_source_asset"),
        ForeignKeyConstraint(["tenant_id", "source_source_asset_id"], ["source_assets.tenant_id", "source_assets.id"], ondelete="RESTRICT", name="fk_image_generation_runs_source_source_asset"),
        ForeignKeyConstraint(["tenant_id", "output_asset_id"], ["assets.tenant_id", "assets.id"], ondelete="RESTRICT", name="fk_image_generation_runs_output_asset"),
        UniqueConstraint("tenant_id", "created_by_user_id", "client_request_id", name="uq_image_generation_runs_client_request"),
        CheckConstraint("operation = 'square_expand'", name="ck_image_generation_runs_operation"),
        CheckConstraint("provider IN ('adobe_firefly', 'cloudflare_sd', 'gemini')", name="ck_image_generation_runs_provider"),
        CheckConstraint("preservation_mode IN ('strict_expand', 'semantic_expand')", name="ck_image_generation_runs_preservation"),
        CheckConstraint("target_width IN (1024, 2048) AND target_height = target_width", name="ck_image_generation_runs_target"),
        CheckConstraint("status IN ('queued', 'preparing', 'submitted', 'running', 'storing', 'completed', 'failed', 'cancelled')", name="ck_image_generation_runs_status"),
        Index("ix_image_generation_runs_tenant_status", "tenant_id", "status", "created_at"),
        Index("ix_image_generation_runs_source", "tenant_id", "source_asset_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_source_asset_id: Mapped[str | None] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(32), nullable=False, default="square_expand")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_model: Mapped[str | None] = mapped_column(String(128))
    preservation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    target_width: Mapped[int] = mapped_column(Integer, nullable=False)
    target_height: Mapped[int] = mapped_column(Integer, nullable=False)
    source_width: Mapped[int] = mapped_column(Integer, nullable=False)
    source_height: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_width: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_height: Mapped[int] = mapped_column(Integer, nullable=False)
    left: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    right: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bottom: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    provider_upload_id: Mapped[str | None] = mapped_column(String(255))
    provider_job_id: Mapped[str | None] = mapped_column(String(255))
    provider_interaction_id: Mapped[str | None] = mapped_column(String(255))
    provider_status_url: Mapped[str | None] = mapped_column(Text)
    provider_cancel_url: Mapped[str | None] = mapped_column(Text)
    output_asset_id: Mapped[str | None] = mapped_column(String(36))
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
