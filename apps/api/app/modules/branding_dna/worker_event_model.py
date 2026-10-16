"""Tenant- and agent-scoped, append-only worker lifecycle event receipts.

These are restricted operational signals, not product data, scan results, or
a worker command channel. No scanner can be enabled through this table.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandWorkerEventModel(Base):
    __tablename__ = "bc_worker_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "event_id", name="uq_bc_worker_event_replay"),
        CheckConstraint("kind IN ('started','stopped','heartbeat_failure','heartbeat_recovered')", name="ck_bc_worker_event_kind"),
        Index("ix_bc_worker_events_tenant_received", "tenant_id", "received_at"),
        Index("ix_bc_worker_events_agent_received", "tenant_id", "agent_id", "received_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Agent IDs are UUIDs; the application checks the agent tenant on ingress.
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("bc_browser_agents.id", ondelete="RESTRICT"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail_code: Mapped[str] = mapped_column(String(32), nullable=False)
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
