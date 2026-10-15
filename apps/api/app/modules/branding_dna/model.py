"""Tenant-scoped Branding DNA source registry. Scans are disabled until an agent is enrolled."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

class BrandShopModel(Base):
    __tablename__ = "bc_shops"
    __table_args__ = (
        UniqueConstraint("tenant_id", "platform", "region", "canonical_shop_key",
                         name="uq_bc_shop_tenant_platform_region"),
        Index("ix_bc_shops_tenant_updated", "tenant_id", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    region: Mapped[str] = mapped_column(String(20), nullable=False)
    canonical_shop_key: Mapped[str] = mapped_column(String(255), nullable=False)
    storefront_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    display_name: Mapped[str] = mapped_column(String(90), nullable=False)
    access_mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc)

class BrandWorkerModel(Base):
    __tablename__ = "bc_browser_agents"
    __table_args__ = (Index("ix_bc_agent_tenant_status", "tenant_id", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str] = mapped_column(String(90), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unregistered")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
