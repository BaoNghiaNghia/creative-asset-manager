from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LogApplicationModel(Base):
    __tablename__ = "log_applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_log_applications_tenant_slug"),
        UniqueConstraint("secret_hash", name="uq_log_applications_secret_hash"),
        UniqueConstraint("tenant_id", "id", name="uq_log_applications_tenant_id"),
        Index("ix_log_applications_tenant_active", "tenant_id", "active"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_schema_json: Mapped[dict | None] = mapped_column(JSON)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationLogModel(Base):
    __tablename__ = "application_logs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "application_id"], ["log_applications.tenant_id", "log_applications.id"], ondelete="CASCADE", name="fk_application_logs_tenant_application"),
        UniqueConstraint("application_id", "idempotency_key", name="uq_application_logs_app_idempotency"),
        CheckConstraint("level IN ('trace','debug','info','warning','error','critical')", name="ck_application_logs_level"),
        Index("ix_application_logs_app_received", "tenant_id", "application_id", "received_at"),
        Index("ix_application_logs_expires", "expires_at"),
        Index("ix_application_logs_trace", "application_id", "trace_id"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: f"log_{uuid4().hex}")
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    application_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(255))
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
