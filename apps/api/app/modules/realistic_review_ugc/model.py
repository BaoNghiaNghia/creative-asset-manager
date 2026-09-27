from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RrugcCampaignModel(Base):
    __tablename__ = "rrugc_campaigns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_campaign_tenant_id"),
        Index("ix_rrugc_campaign_tenant_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_scroll_batches: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    auto_import: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_head_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    max_head_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.45)
    min_smile_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.65)
    max_head_occlusion: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    max_ai_risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    min_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.55)
    min_ugc_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.55)
    min_product_fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.55)
    require_head_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reject_headwear: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    scout_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scout_status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    scout_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RrugcProductModel(Base):
    __tablename__ = "rrugc_products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_rrugc_product_tenant_sku"),
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_product_tenant_id"),
        Index("ix_rrugc_product_tenant_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    product_type: Mapped[str] = mapped_column(String(64), nullable=False, default="hat")
    color: Mapped[str | None] = mapped_column(String(120))
    material: Mapped[str | None] = mapped_column(String(200))
    crown_profile: Mapped[str | None] = mapped_column(String(64))
    crown_height_mm: Mapped[float | None] = mapped_column(Float)
    brim_style: Mapped[str | None] = mapped_column(String(64))
    brim_length_mm: Mapped[float | None] = mapped_column(Float)
    circumference_mm: Mapped[float | None] = mapped_column(Float)
    logo_position: Mapped[str | None] = mapped_column(String(120))
    fit_notes: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcProductReferenceModel(Base):
    __tablename__ = "rrugc_product_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_product_reference_product",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "product_id", "view_type", "version",
            name="uq_rrugc_product_reference_version",
        ),
        Index(
            "ix_rrugc_product_reference_product_view",
            "tenant_id", "product_id", "view_type", "version",
        ),
        Index(
            "ix_rrugc_product_reference_hash",
            "tenant_id", "content_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    view_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    image_format: Mapped[str] = mapped_column(String(16), nullable=False)
    remote_file_id: Mapped[str | None] = mapped_column(String(255))
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    reused_storage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcCandidateModel(Base):
    __tablename__ = "rrugc_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_candidate_campaign",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "campaign_id", "source_key",
            name="uq_rrugc_candidate_source",
        ),
        Index(
            "ix_rrugc_candidate_campaign_status",
            "tenant_id", "campaign_id", "status", "created_at",
        ),
        Index(
            "ix_rrugc_candidate_content_hash",
            "tenant_id", "content_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    pin_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="analysis_queued")
    analysis_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    import_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    people_count: Mapped[int | None] = mapped_column(Integer)
    primary_head_ratio: Mapped[float | None] = mapped_column(Float)
    smile_score: Mapped[float | None] = mapped_column(Float)
    head_visible: Mapped[bool | None] = mapped_column(Boolean)
    existing_headwear: Mapped[bool | None] = mapped_column(Boolean)
    head_occlusion: Mapped[float | None] = mapped_column(Float)
    mobile_ugc_score: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    ai_risk_score: Mapped[float | None] = mapped_column(Float)
    product_fit_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float | None] = mapped_column(Float)
    reject_reason: Mapped[str | None] = mapped_column(String(64))
    analyzer_provider: Mapped[str | None] = mapped_column(String(64))
    analyzer_model: Mapped[str | None] = mapped_column(String(128))
    analyzer_version: Mapped[str | None] = mapped_column(String(64))
    analysis_summary: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    image_format: Mapped[str | None] = mapped_column(String(16))
    remote_file_id: Mapped[str | None] = mapped_column(String(255))
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
