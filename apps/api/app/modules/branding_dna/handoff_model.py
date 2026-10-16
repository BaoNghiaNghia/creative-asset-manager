"""Unverified browser handoff candidates awaiting independent source/rights checks.

This table does not represent Brand DNA evidence, successful scans, seller
permission, verified sales or training rights. Nothing is dispatched.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandHandoffCandidateModel(Base):
    __tablename__ = "bc_handoff_candidates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "shop_id", "candidate_sha256", name="uq_bc_handoff_candidate_replay"),
        Index("ix_bc_handoff_tenant_shop", "tenant_id", "shop_id", "created_at"),
        CheckConstraint(
            "status IN ('pending_source_review','needs_authorized_export','rejected')",
            name="ck_bc_handoff_review_status",
        ),
        CheckConstraint("image_reference_count BETWEEN 0 AND 12", name="ck_bc_handoff_image_count"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    packet_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_file: Mapped[str] = mapped_column(String(110), nullable=False)
    listing_key: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    image_reference_count: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_note: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    review_note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(35), nullable=False, default="pending_source_review")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
