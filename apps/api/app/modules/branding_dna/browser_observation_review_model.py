"""Immutable, tenant-scoped human reviews of quarantined browser observations."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Index, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandBrowserObservationReviewModel(Base):
    __tablename__ = "bc_browser_observation_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "observation_id"],
            ["bc_browser_observations.tenant_id",
             "bc_browser_observations.shop_id",
             "bc_browser_observations.id"],
            name="fk_bc_browser_review_observation_scope",
            ondelete="RESTRICT",
        ),
        Index("ix_bc_browser_reviews_scope_date",
              "tenant_id", "shop_id", "observation_id", "created_at"),
        CheckConstraint(
            "decision IN ('needs_authorized_source','metadata_consistent','rejected')",
            name="ck_bc_browser_review_decision",
        ),
        CheckConstraint(
            "rights_basis IN ('unknown','consent_reference_recorded','permission_missing')",
            name="ck_bc_browser_review_rights_basis",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    observation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rights_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc)
