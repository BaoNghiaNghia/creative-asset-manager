"""Append-only, tenant-scoped quarantined result receipts.

Receipts prove only that CAM durably stored unverified metadata. They do NOT
prove marketplace origin, authorization, creative ownership or media rights.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandScanResultReceiptModel(Base):
    __tablename__ = "bc_scan_result_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["bc_scan_tasks.tenant_id", "bc_scan_tasks.id"],
            name="fk_bc_scan_result_task_tenant", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id",
             "bc_listing_snapshots.id"],
            name="fk_bc_scan_result_snapshot_scope", ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "task_id", "lease_generation",
            name="uq_bc_scan_result_task_generation",
        ),
        UniqueConstraint(
            "tenant_id", "client_receipt_id",
            name="uq_bc_scan_result_client_receipt",
        ),
        Index("ix_bc_scan_result_shop_received", "tenant_id", "shop_id", "received_at"),
        CheckConstraint(
            "lease_generation BETWEEN 1 AND 1000000",
            name="ck_bc_scan_result_generation",
        ),
        CheckConstraint(
            "status = 'quarantined_unverified'",
            name="ck_bc_scan_result_quarantined",
        ),
        CheckConstraint(
            "gallery_count BETWEEN 1 AND 20 AND page_count BETWEEN 1 AND 5",
            name="ck_bc_scan_result_budget",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lease_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    client_receipt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Never store source URLs, binary images, HTML, browser data or credentials.
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    gallery_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False,
                                         default="quarantined_unverified")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                    nullable=False, default=now_utc)
