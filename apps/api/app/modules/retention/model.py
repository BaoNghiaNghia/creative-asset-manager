from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RetentionCleanupRunModel(Base):
    __tablename__ = "retention_cleanup_runs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')", name="ck_retention_cleanup_runs_status"),
        CheckConstraint("max_rows > 0", name="ck_retention_cleanup_runs_max_rows"),
        Index("ix_retention_cleanup_runs_tenant_status", "tenant_id", "status"),
        Index(
            "uq_retention_cleanup_runs_active_scope", "tenant_id", "policy_name",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_name: Mapped[str] = mapped_column(String(100), nullable=False, default="default")
    record_types_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cursor_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    counts_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_json: Mapped[dict | None] = mapped_column(JSON)
