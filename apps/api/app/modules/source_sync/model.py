from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceSyncRunModel(Base):
    __tablename__ = "source_sync_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="CASCADE",
            name="fk_source_sync_runs_tenant_source",
        ),
        UniqueConstraint("tenant_id", "external_source_id", "generation", name="uq_source_sync_runs_generation"),
        CheckConstraint("mode IN ('full', 'incremental')", name="ck_source_sync_runs_mode"),
        CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled')", name="ck_source_sync_runs_status"),
        Index("ix_source_sync_runs_source_status", "tenant_id", "external_source_id", "status"),
        Index(
            "uq_source_sync_runs_active_full", "tenant_id", "external_source_id",
            unique=True,
            postgresql_where=text("mode = 'full' AND status = 'running'"),
            sqlite_where=text("mode = 'full' AND status = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="full")
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    checkpoint_cursor: Mapped[str | None] = mapped_column(Text)
    pages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_json: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
