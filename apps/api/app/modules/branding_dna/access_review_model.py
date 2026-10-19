"""Append-only documentary review, intentionally not a browser access grant."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandShopAccessReviewModel(Base):
    __tablename__ = "bc_shop_access_reviews"
    __table_args__ = (
        Index("ix_bc_access_review_tenant_shop_date", "tenant_id", "shop_id", "created_at"),
        Index("ix_bc_access_review_declaration_date", "declaration_id", "created_at"),
        CheckConstraint(
            "decision IN ('needs_documentation', 'rejected', 'documentation_noted')",
            name="ck_bc_access_review_decision",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False
    )
    declaration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bc_shop_access_declarations.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
