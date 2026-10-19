"""Immutable per-listing human evidence assessments, not independent verification."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint, Index, String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandEvidenceAssessmentModel(Base):
    __tablename__ = "bc_listing_evidence_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id",
             "bc_listing_snapshots.id"],
            name="fk_bc_evidence_assessment_snapshot_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('needs_independent_source','metadata_consistent','rejected')",
            name="ck_bc_evidence_assessment_decision",
        ),
        CheckConstraint(
            "rights_basis IN ('unknown','consent_reference_recorded','permission_missing')",
            name="ck_bc_evidence_assessment_rights_basis",
        ),
        Index(
            "ix_bc_evidence_assessments_scope_date",
            "tenant_id", "shop_id", "snapshot_id", "created_at",
        ),
    )
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rights_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(160))
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc
    )
