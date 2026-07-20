from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantProcessingPolicyModel(Base):
    __tablename__ = "tenant_processing_policies"
    __table_args__ = (
        CheckConstraint("total_active_jobs_limit > 0", name="ck_tenant_policy_total_limit"),
        CheckConstraint("ai_active_jobs_limit > 0", name="ck_tenant_policy_ai_limit"),
        CheckConstraint("source_active_jobs_limit > 0", name="ck_tenant_policy_source_limit"),
        CheckConstraint("storage_active_jobs_limit > 0", name="ck_tenant_policy_storage_limit"),
        CheckConstraint("total_active_jobs >= 0", name="ck_tenant_policy_total_active"),
        CheckConstraint("ai_active_jobs >= 0", name="ck_tenant_policy_ai_active"),
        CheckConstraint("source_active_jobs >= 0", name="ck_tenant_policy_source_active"),
        CheckConstraint("storage_active_jobs >= 0", name="ck_tenant_policy_storage_active"),
        CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="ck_tenant_policy_rollout_percentage"),
        CheckConstraint("rollout_mode IN ('explicit', 'percentage')", name="ck_tenant_policy_rollout_mode"),
        Index("ix_tenant_processing_policies_eligible", "processing_paused", "pipeline_enabled", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    pipeline_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    download_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    managed_storage_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_analysis_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    search_v2_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sidecar_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processing_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_reason: Mapped[str | None] = mapped_column(Text)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_by: Mapped[str | None] = mapped_column(String(255))
    rollout_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="explicit")
    rollout_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    total_active_jobs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    ai_active_jobs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_active_jobs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    storage_active_jobs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    total_active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class TenantProviderPolicyModel(Base):
    __tablename__ = "tenant_provider_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_key", "provider_scope", name="uq_tenant_provider_policy_identity"),
        CheckConstraint("active_jobs_limit > 0", name="ck_tenant_provider_policy_limit"),
        CheckConstraint("active_jobs >= 0", name="ck_tenant_provider_policy_active"),
        Index("ix_tenant_provider_policy_eligible", "tenant_id", "provider_key", "provider_scope", "processing_paused"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    processing_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_reason: Mapped[str | None] = mapped_column(Text)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_by: Mapped[str | None] = mapped_column(String(255))
    active_jobs_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ProcessingPolicyAuditModel(Base):
    __tablename__ = "processing_policy_audits"
    __table_args__ = (
        Index("ix_processing_policy_audits_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(64))
    provider_scope: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    old_policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    new_policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
