"""Durable per-listing Muse queue with no dependency on Windows runtime availability."""
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class CamMuseJob(Base):
    __tablename__ = "cam_muse_jobs"
    __table_args__ = (
        Index("ix_cam_muse_status", "tenant_id", "status", "picked_at"),
        ForeignKeyConstraint(
            ["tenant_id", "listing_key"], ["etsy_spy_picked.tenant_id", "etsy_spy_picked.listing_key"],
            name="fk_cam_muse_picked", ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    listing_key: Mapped[str] = mapped_column(String(150), primary_key=True)
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(700), nullable=False)
    keyword: Mapped[str | None] = mapped_column(String(220))
    artwork_sha256: Mapped[str | None] = mapped_column(String(64))
    artwork_asset_path: Mapped[str | None] = mapped_column(String(250))
    product_image_url: Mapped[str | None] = mapped_column(String(1024))
    source_state: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="qa_hold")
    lease_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    output_receipts: Mapped[dict | None] = mapped_column(JSON())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
