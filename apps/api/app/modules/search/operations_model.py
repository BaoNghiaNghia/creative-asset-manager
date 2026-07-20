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


class SearchOperationRunModel(Base):
    __tablename__ = "search_operation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_search_operation_runs_tenant_id"),
        CheckConstraint(
            "operation_type IN ('rebuild_projections', 'reindex_assets', 'rebuild_and_reindex')",
            name="ck_search_operation_runs_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_search_operation_runs_status",
        ),
        CheckConstraint("page_size > 0 AND page_size <= 500", name="ck_search_operation_runs_page"),
        Index("ix_search_operation_runs_tenant_status", "tenant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    filters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    target_projection_version: Mapped[str] = mapped_column(String(100), nullable=False)
    target_index: Mapped[str | None] = mapped_column(String(255))
    alias_switch_json: Mapped[dict | None] = mapped_column(JSON)
    page_size: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cursor_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_analysis_id: Mapped[str | None] = mapped_column(String(36))
    scanned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchOperationItemModel(Base):
    __tablename__ = "search_operation_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["search_operation_runs.tenant_id", "search_operation_runs.id"],
            ondelete="CASCADE",
            name="fk_search_operation_items_run",
        ),
        ForeignKeyConstraint(
            ["analysis_id"],
            ["asset_ai_analyses.id"],
            ondelete="CASCADE",
            name="fk_search_operation_items_analysis",
        ),
        UniqueConstraint("run_id", "analysis_id", name="uq_search_operation_items_analysis"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'skipped')",
            name="ck_search_operation_items_status",
        ),
        Index("ix_search_operation_items_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
