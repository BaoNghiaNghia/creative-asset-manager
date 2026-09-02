from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
# Register the tenant table for isolated provider/test metadata creation.
from app.modules.auth_persistence.model import TenantModel as _TenantModel


def _id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CreativeAiCredentialModel(Base):
    """Encrypted tenant-local Creative AI provider secret; never an OAuth token."""
    __tablename__ = "creative_ai_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_creative_ai_credentials_tenant_provider"),
        CheckConstraint("provider IN ('gemini', 'gemini_video', 'gemini_image')", name="ck_creative_ai_credentials_provider"),
        CheckConstraint("status IN ('active','disabled')", name="ck_creative_ai_credentials_status"),
        CheckConstraint("length(secret_fingerprint) = 64", name="ck_creative_ai_credentials_fingerprint"),
        CheckConstraint("length(secret_last4) = 4", name="ck_creative_ai_credentials_last4"),
        Index("ix_creative_ai_credentials_tenant_status", "tenant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="gemini")
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(String(255))


class CreativeAiCredentialAuditModel(Base):
    __tablename__ = "creative_ai_credential_audits"
    __table_args__ = (Index("ix_creative_ai_credential_audits_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="gemini")
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_fingerprint: Mapped[str | None] = mapped_column(String(64))
    new_fingerprint: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
