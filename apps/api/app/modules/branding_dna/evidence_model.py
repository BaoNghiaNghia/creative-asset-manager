"""Append-only, tenant-scoped listing/gallery evidence from authorized exports.

This is not a browser scanner. Imported observations remain explicitly
unverified until an independent allowed source adapter validates them.
"""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandListingSnapshotModel(Base):
    __tablename__ = "bc_listing_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "shop_id", "listing_key", "source_revision",
                         name="uq_bc_listing_snapshot_revision"),
        UniqueConstraint("tenant_id", "shop_id", "id", name="uq_bc_listing_snapshot_scope"),
        Index("ix_bc_listing_snapshots_shop_date", "tenant_id", "shop_id", "captured_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False)
    listing_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    export_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_state: Mapped[str] = mapped_column(String(32), nullable=False, default="imported_unverified")
    coverage_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    imported_by: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class BrandListingImageModel(Base):
    __tablename__ = "bc_listing_images"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id", "bc_listing_snapshots.id"],
            name="fk_bc_listing_images_snapshot_scope", ondelete="RESTRICT",
        ),
        UniqueConstraint("snapshot_id", "sequence_index", name="uq_bc_listing_image_gallery_order"),
        Index("ix_bc_listing_images_snapshot_order", "snapshot_id", "sequence_index"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    variant_key: Mapped[str | None] = mapped_column(String(128))
    rights_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
