"""BD-01 durable scan-task staging (execution intentionally disabled)."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandScanTaskModel(Base):
    __tablename__ = "bc_scan_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id", "bc_listing_snapshots.id"],
            name="fk_bc_scan_task_snapshot_scope", ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "shop_id", "snapshot_id", "task_kind",
                         name="uq_bc_scan_task_idempotency"),
        Index("ix_bc_scan_task_queue", "tenant_id", "status", "retry_after", "created_at"),
        Index("ix_bc_scan_task_shop", "tenant_id", "shop_id", "created_at"),
        CheckConstraint("attempts >= 0 AND attempts <= max_attempts AND max_attempts BETWEEN 1 AND 5",
                        name="ck_bc_scan_task_attempts"),
        CheckConstraint("lease_generation >= 0 AND checkpoint_seq >= 0",
                        name="ck_bc_scan_task_fencing"),
        CheckConstraint(
            "status IN ('staged','pending','leased','retry','blocked','completed','cancelled')",
            name="ck_bc_scan_task_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    task_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="verify_listing_evidence")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="staged")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_id: Mapped[str | None] = mapped_column(String(36))
    agent_id: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
