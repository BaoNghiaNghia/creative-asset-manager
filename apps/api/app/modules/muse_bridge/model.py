"""Durable per-listing Muse queue with no dependency on Windows runtime availability."""
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint
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

class CamMuseCommand(Base):
    """Bounded commands issued by the local CAM operator and processed by Muse.

    Commands cannot invoke arbitrary functions, shell commands, browser URLs or
    OpenAI APIs. Their state is separate from CAM listing/video delivery state.
    """
    __tablename__ = "cam_muse_commands"
    __table_args__ = (
        Index("ix_cam_muse_command_pending", "tenant_id", "status", "created_at"),
        UniqueConstraint("tenant_id", "request_id",
                         name="uq_cam_muse_command_request_id"),
        CheckConstraint("action IN ('create_video','review_artwork')",
                        name="ck_cam_muse_command_action"),
        CheckConstraint("status IN ('pending','claimed','receipt_submitted','reviewed','failed')",
                        name="ck_cam_muse_command_status"),
        CheckConstraint("phase IN ('review_artwork','create_video')",
                        name="ck_cam_muse_command_phase"),
        CheckConstraint("attempts >= 0 AND attempts <= 5",
                        name="ck_cam_muse_command_attempts"),
        ForeignKeyConstraint(
            ["tenant_id", "listing_key"],
            ["cam_muse_jobs.tenant_id", "cam_muse_jobs.listing_key"],
            name="fk_cam_muse_command_listing", ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    listing_key: Mapped[str] = mapped_column(String(150), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    artwork_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_hash: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    details: Mapped[dict | None] = mapped_column(JSON())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
