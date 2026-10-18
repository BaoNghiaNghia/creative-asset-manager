"""Shop-scoped, revocable review records for scanner policy paperwork.

Human review of an uploaded policy file is not independent marketplace authorization.
No model in this module can enable a scan or issue a worker lease.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandScanAuthorizationReviewModel(Base):
    __tablename__ = "bc_scan_authorization_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "document_id"],
            ["bc_knowledge_documents.tenant_id", "bc_knowledge_documents.shop_id",
             "bc_knowledge_documents.id"],
            name="fk_bc_scan_review_policy_doc_scope", ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "shop_id", "document_id",
                         name="uq_bc_scan_review_source_doc"),
        Index("ix_bc_scan_review_shop_created", "tenant_id", "shop_id", "created_at"),
        CheckConstraint("status IN ('pending_review','human_reviewed','rejected','revoked')",
                        name="ck_bc_scan_review_status"),
        CheckConstraint("policy_scope IN ('etsy_written_access','amazon_agent_policy')",
                        name="ck_bc_scan_review_scope"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    storefront_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    submitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    review_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
