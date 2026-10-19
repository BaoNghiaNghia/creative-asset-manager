"""Versioned, immutable DNA fact outline and append-only human decisions.

Neither table represents an approved Brand DNA or grants downstream permissions.
"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandDnaOutlineVersionModel(Base):
    __tablename__ = "bc_dna_outline_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["shop_id"], ["bc_shops.id"], ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "shop_id", "version_number",
                         name="uq_bc_dna_outline_shop_version"),
        UniqueConstraint("tenant_id", "shop_id", "input_fingerprint",
                         name="uq_bc_dna_outline_fingerprint"),
        Index("ix_bc_dna_outline_tenant_shop", "tenant_id", "shop_id", "version_number"),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    outline_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )


class BrandDnaOutlineReviewModel(Base):
    __tablename__ = "bc_dna_outline_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id"], ["bc_dna_outline_versions.id"], ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('needs_changes','fact_outline_reviewed','rejected')",
            name="ck_bc_dna_outline_review_decision",
        ),
        Index("ix_bc_dna_outline_reviews_date", "version_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
