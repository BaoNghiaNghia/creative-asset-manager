"""Tenant-wide Cap Stack library, isolated from campaign/reference group IDs."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, Float, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class RrugcCapStackControlModel(Base):
    __tablename__ = "rrugc_cap_stack_controls"
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_agent_id: Mapped[str | None] = mapped_column(String(36))
    lease_run_id: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_result: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)


class RrugcCapStackImageModel(Base):
    __tablename__ = "rrugc_cap_stack_images"
    __table_args__ = (
        UniqueConstraint("tenant_id", "pin_key", name="uq_rrugc_cap_stack_tenant_pin"),
        Index("ix_rrugc_cap_stack_status_created", "tenant_id", "manual_label", "created_at"),
        Index("ix_rrugc_cap_stack_source_query", "tenant_id", "source_query"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    pin_key: Mapped[str] = mapped_column(String(128), nullable=False)
    pin_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text)
    source_query: Mapped[str | None] = mapped_column(String(256))
    discovered_by_agent_id: Mapped[str | None] = mapped_column(String(36))
    # AI count is deliberately unknown until a vision worker verifies it.
    caps_detected: Mapped[int | None] = mapped_column(Integer)
    count_confidence: Mapped[float | None] = mapped_column(Float)
    count_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    analysis_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_cap_stack: Mapped[bool | None] = mapped_column(Boolean)
    ai_suspected: Mapped[bool | None] = mapped_column(Boolean)
    ai_suspicion_confidence: Mapped[float | None] = mapped_column(Float)
    visual_summary: Mapped[str | None] = mapped_column(Text)
    analysis_model: Mapped[str | None] = mapped_column(String(128))
    analysis_error_code: Mapped[str | None] = mapped_column(String(100))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_caps_count: Mapped[int | None] = mapped_column(Integer)
    manual_label: Mapped[str | None] = mapped_column(String(12))
    feedback_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)
