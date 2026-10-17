"""Separate tenant-scoped presence for paired Branding DNA Scout processes.

Never changes the RRUGC Scout's status, scheduling, or credentials.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.modules.branding_dna.model import now_utc


class BrandScoutSessionModel(Base):
    __tablename__ = "bc_scout_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"], ["rrugc_scout_agents.tenant_id", "rrugc_scout_agents.id"],
            name="fk_bc_scout_session_paired", ondelete="CASCADE",
        ),
        Index("ix_bc_scout_sessions_tenant_last_seen", "tenant_id", "last_seen_at"),
        CheckConstraint("shop_count >= 0 AND shop_count <= 200",
                        name="ck_bc_scout_sessions_shop_count"),
    )
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    shop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=now_utc)
