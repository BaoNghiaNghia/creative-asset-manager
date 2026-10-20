"""Quarantined partial DOM observations, not independently verified evidence."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandBrowserObservationModel(Base):
    __tablename__ = "bc_browser_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "shop_id", "report_key",
                         name="uq_bc_browser_observation_report"),
        UniqueConstraint("tenant_id", "shop_id", "id",
                         name="uq_bc_browser_observation_scope"),
        Index("ix_bc_browser_observations_shop_date",
              "tenant_id", "shop_id", "created_at"),
        CheckConstraint("status = 'preview_completed'",
                        name="ck_bc_browser_observation_status"),
        CheckConstraint("platform IN ('amazon','etsy')",
                        name="ck_bc_browser_observation_platform"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False)
    report_key: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    shop_url_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    items_json: Mapped[str] = mapped_column(Text, nullable=False)
    imported_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc)
