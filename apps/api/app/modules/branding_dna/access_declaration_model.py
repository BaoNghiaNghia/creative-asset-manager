"""Append-only operator declarations; never confer marketplace browser access."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandShopAccessDeclarationModel(Base):
    __tablename__ = "bc_shop_access_declarations"
    __table_args__ = (
        Index("ix_bc_shop_access_tenant_shop_date", "tenant_id", "shop_id", "created_at"),
        CheckConstraint(
            "relationship IN ('owned', 'authorized', 'competitor_research', 'unknown')",
            name="ck_bc_shop_access_relationship",
        ),
        CheckConstraint(
            "status IN ('pending_review','recorded_only')",
            name="ck_bc_shop_access_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False)
    relationship: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    declared_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
