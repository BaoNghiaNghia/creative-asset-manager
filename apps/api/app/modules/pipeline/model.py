from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.pipeline.state import PipelineState


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetPipelineModel(Base):
    __tablename__ = "asset_pipelines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "correlation_id", name="uq_asset_pipelines_tenant_correlation"),
        UniqueConstraint("tenant_id", "origin_type", "origin_id", name="uq_asset_pipelines_tenant_origin"),
        ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["source_assets.tenant_id", "source_assets.id"], ondelete="SET NULL", name="fk_asset_pipelines_source_asset"),
        ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], ondelete="SET NULL", name="fk_asset_pipelines_asset"),
        CheckConstraint("origin_type IN ('source_asset', 'ingestion_item')", name="ck_asset_pipelines_origin_type"),
        CheckConstraint("state IN (%s)" % ",".join(repr(item.value) for item in PipelineState), name="ck_asset_pipelines_state"),
        Index("ix_asset_pipelines_status", "tenant_id", "state", "updated_at"),
        Index("ix_asset_pipelines_asset", "tenant_id", "asset_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_asset_id: Mapped[str | None] = mapped_column(String(36))
    asset_id: Mapped[str | None] = mapped_column(String(36))
    analysis_id: Mapped[str | None] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=PipelineState.DISCOVERED.value)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    projection_version: Mapped[str | None] = mapped_column(String(64))
    projection_checksum: Mapped[str | None] = mapped_column(String(64))
    indexed_projection_version: Mapped[str | None] = mapped_column(String(64))
    indexed_projection_checksum: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    failure_retryable: Mapped[bool | None] = mapped_column(Boolean)
    status_data_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
