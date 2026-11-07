"""Tenant-scoped read projection of the independent Windows Etsy Spy databases."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EtsySpyAgent(Base):
    __tablename__ = "etsy_spy_agents"
    __table_args__ = (Index("ix_etsy_spy_agents_tenant_seen", "tenant_id", "last_seen_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    machine_label: Mapped[str | None] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extension_version: Mapped[str | None] = mapped_column(String(40))
    local_api_version: Mapped[str | None] = mapped_column(String(40))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inventory_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inventory_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    last_cursor: Mapped[str | None] = mapped_column(String(180))
    last_error: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EtsySpyBlacklist(Base):
    """Tenant-owned phrases for listing visibility and future Windows Scout exclusion."""
    __tablename__ = "etsy_spy_blacklist"
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    phrase: Mapped[str] = mapped_column(String(120), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EtsySpyWinKeyword(Base):
    """Passive, tenant-owned keyword notes; never used by filtering or Scout."""
    __tablename__ = "etsy_spy_win_keywords"
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    keyword_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    keyword: Mapped[str] = mapped_column(String(220), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EtsySpyPicked(Base):
    """User-picked Etsy listings, stable across sync sessions and source agents."""
    __tablename__ = "etsy_spy_picked"
    __table_args__ = (
        Index("ix_etsy_spy_picked_tenant_at", "tenant_id", "picked_at"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    listing_key: Mapped[str] = mapped_column(String(150), primary_key=True)
    picked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )


class EtsySpyBatch(Base):
    __tablename__ = "etsy_spy_sync_batches"
    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "batch_id", name="uq_etsy_spy_batch"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(96), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[str | None] = mapped_column(String(180))
    row_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EtsySpySession(Base):
    __tablename__ = "etsy_spy_sessions"
    __table_args__ = (Index("ix_etsy_spy_sessions_tenant_updated", "tenant_id", "updated_at"),)
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_session_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    mode: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(50))
    phase: Mapped[str | None] = mapped_column(String(80))
    keyword: Mapped[str | None] = mapped_column(String(220))
    revision: Mapped[int] = mapped_column(Integer(), default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EtsySpyKeyword(Base):
    __tablename__ = "etsy_spy_keywords"
    __table_args__ = (Index("ix_etsy_spy_keywords_tenant_keyword", "tenant_id", "keyword"),)
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_session_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    keyword: Mapped[str] = mapped_column(String(220), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(150))
    depth: Mapped[int | None] = mapped_column(Integer())
    priority: Mapped[float | None] = mapped_column(Float())
    status: Mapped[str | None] = mapped_column(String(50))
    reason: Mapped[str | None] = mapped_column(Text())
    evidence_json: Mapped[object | None] = mapped_column(JSON())
    source_url: Mapped[str | None] = mapped_column(Text())
    matched_count: Mapped[int | None] = mapped_column(Integer())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EtsySpyListing(Base):
    __tablename__ = "etsy_spy_listings"
    __table_args__ = (
        Index("ix_etsy_spy_listings_tenant_observed", "tenant_id", "observed_at"),
        Index("ix_etsy_spy_listings_tenant_keyword", "tenant_id", "keyword"),
        Index("ix_etsy_spy_listings_tenant_tm_key", "tenant_id", "agent_id", "tm_key"),
        Index("ix_etsy_spy_listings_active", "tenant_id", "agent_id", "is_current"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_session_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    keyword: Mapped[str] = mapped_column(String(220), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_shop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    listing_key: Mapped[str] = mapped_column(String(150), primary_key=True)
    title: Mapped[str] = mapped_column(String(700), nullable=False)
    url: Mapped[str | None] = mapped_column(Text())
    badge: Mapped[str | None] = mapped_column(String(70))
    search_badge: Mapped[str | None] = mapped_column(String(70))
    source_shop_url: Mapped[str | None] = mapped_column(Text())
    shop_position: Mapped[int | None] = mapped_column(Integer())
    search_position: Mapped[int | None] = mapped_column(Integer())
    bought24h: Mapped[int | None] = mapped_column(Integer())
    views24h: Mapped[int | None] = mapped_column(Integer())
    in_carts: Mapped[int | None] = mapped_column(Integer())
    bestseller: Mapped[bool | None] = mapped_column(Boolean())
    demand_score: Mapped[float | None] = mapped_column(Float())
    tm_key: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[object | None] = mapped_column(JSON())
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EtsySpyTrademark(Base):
    __tablename__ = "etsy_spy_trademark_checks"
    __table_args__ = (
        Index("ix_etsy_spy_tm_tenant_title", "tenant_id", "agent_id", "check_id"),
        Index("ix_etsy_spy_tm_tenant_checked", "tenant_id", "checked_at"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    check_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    keyword: Mapped[str | None] = mapped_column(String(220))
    title: Mapped[str | None] = mapped_column(String(700))
    status: Mapped[str | None] = mapped_column(String(35))
    risk: Mapped[str | None] = mapped_column(String(55))
    live_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    dead_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    unknown_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    matched_json: Mapped[object] = mapped_column(JSON(), nullable=False, default=list)
    source: Mapped[str | None] = mapped_column(String(80))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EtsySpyLog(Base):
    __tablename__ = "etsy_spy_system_logs"
    __table_args__ = (Index("ix_etsy_spy_logs_tenant_at", "tenant_id", "event_at"),)
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    source_session_id: Mapped[str | None] = mapped_column(String(150))
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    level: Mapped[str | None] = mapped_column(String(25))
    source: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[str | None] = mapped_column(String(80))
    action: Mapped[str | None] = mapped_column(String(100))
    phase: Mapped[str | None] = mapped_column(String(100))
    keyword: Mapped[str | None] = mapped_column(String(220))
    message: Mapped[str | None] = mapped_column(Text())
    stack: Mapped[str | None] = mapped_column(Text())