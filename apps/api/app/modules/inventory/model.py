from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _new_id() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InventoryProcessingControlModel(Base):
    __tablename__ = "inventory_processing_controls"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_inventory_controls_tenant"),
        CheckConstraint("max_active_jobs > 0", name="ck_inventory_controls_max_active"),
        CheckConstraint("max_ai_jobs >= 0", name="ck_inventory_controls_max_ai"),
        CheckConstraint(
            "max_ai_jobs <= max_active_jobs",
            name="ck_inventory_controls_ai_within_active",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_ai_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class InventoryAiControlModel(Base):
    """Tenant-scoped AI controls; no Creative governance state is shared."""
    __tablename__ = "inventory_ai_controls"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_inventory_ai_controls_tenant"),
        CheckConstraint("max_concurrent > 0", name="ck_inventory_ai_controls_concurrent"),
        CheckConstraint("min_start_interval_seconds >= 0", name="ck_inventory_ai_controls_interval"),
        CheckConstraint("per_run_limit > 0", name="ck_inventory_ai_controls_per_run"),
        CheckConstraint("daily_budget_micros >= 0", name="ck_inventory_ai_controls_daily_budget"),
        CheckConstraint("monthly_budget_micros >= 0", name="ck_inventory_ai_controls_monthly_budget"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="gemini")
    allowed_models_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    min_start_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    per_run_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    daily_budget_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    monthly_budget_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)
