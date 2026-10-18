"""Immutable, tenant-fenced terminal failure receipts for scan task leases.

Stores no browser output, URL, token, personal data, or product evidence.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandScanFailureReceiptModel(Base):
    __tablename__ = "bc_scan_failure_receipts"
    __table_args__ = (
        Index("ix_bc_scan_receipt_tenant_task", "tenant_id", "task_id"),
        ForeignKeyConstraint(["tenant_id", "task_id"],
            ["bc_scan_tasks.tenant_id", "bc_scan_tasks.id"],
            name="fk_bc_scan_receipt_task_scope", ondelete="RESTRICT"),
        CheckConstraint("lease_generation BETWEEN 1 AND 1000000", name="ck_bc_scan_receipt_generation"),
        CheckConstraint("status IN ('retry','blocked')", name="ck_bc_scan_receipt_status"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lease_generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    error_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
