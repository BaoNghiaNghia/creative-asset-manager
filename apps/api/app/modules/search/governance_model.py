from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActiveAssetAnalysisModel(Base):
    __tablename__ = "active_asset_analyses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "asset_id", "metadata_profile_id", "search_context", name="uq_active_asset_analysis_context"),
        Index("ix_active_asset_analyses_analysis", "tenant_id", "analysis_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    search_context: Mapped[str] = mapped_column(String(64), nullable=False, default="search_v2")
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=False)
    activated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    activation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ActiveAnalysisAuditModel(Base):
    __tablename__ = "active_analysis_audits"
    __table_args__ = (Index("ix_active_analysis_audits_tenant_asset", "tenant_id", "asset_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    metadata_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    search_context: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_analysis_id: Mapped[str | None] = mapped_column(String(36))
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TenantSearchShadowPolicyModel(Base):
    __tablename__ = "tenant_search_shadow_policies"
    __table_args__ = (
        CheckConstraint("primary_version IN ('v1','v2')", name="ck_shadow_primary_version"),
        CheckConstraint("shadow_version IN ('v1','v2')", name="ck_shadow_secondary_version"),
        CheckConstraint("primary_version <> shadow_version", name="ck_shadow_distinct_versions"),
        CheckConstraint("sample_percentage >= 0 AND sample_percentage <= 100", name="ck_shadow_sample_percentage"),
        CheckConstraint("timeout_ms > 0 AND timeout_ms <= 10000", name="ck_shadow_timeout"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    primary_version: Mapped[str] = mapped_column(String(8), nullable=False, default="v1")
    shadow_version: Mapped[str] = mapped_column(String(8), nullable=False, default="v2")
    sample_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=250)
    persist_raw_query: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_query_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    report_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class SearchShadowObservationModel(Base):
    __tablename__ = "search_shadow_observations"
    __table_args__ = (Index("ix_shadow_observations_report", "tenant_id", "occurred_at", "query_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_query: Mapped[str | None] = mapped_column(Text)
    query_type: Mapped[str] = mapped_column(String(32), nullable=False)
    query_features_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_profile: Mapped[str | None] = mapped_column(String(255))
    primary_version: Mapped[str] = mapped_column(String(8), nullable=False)
    shadow_version: Mapped[str] = mapped_column(String(8), nullable=False)
    primary_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    shadow_latency_ms: Mapped[int | None] = mapped_column(Integer)
    primary_count: Mapped[int] = mapped_column(Integer, nullable=False)
    shadow_count: Mapped[int | None] = mapped_column(Integer)
    top_k_overlap: Mapped[float | None] = mapped_column(Float)
    rank_correlation: Mapped[float | None] = mapped_column(Float)
    top_result_agrees: Mapped[bool | None] = mapped_column(Boolean)
    zero_result_disagrees: Mapped[bool | None] = mapped_column(Boolean)
    error_category: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class SearchIndexRecordModel(Base):
    __tablename__ = "search_index_records"
    __table_args__ = (
        UniqueConstraint("physical_index_name", name="uq_search_index_record_name"),
        CheckConstraint("lifecycle_state IN ('building','validating','active','previous','retired','deletion_pending','deleted','failed')", name="ck_search_index_lifecycle_state"),
        Index("ix_search_index_lifecycle", "index_prefix", "lifecycle_state", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    physical_index_name: Mapped[str] = mapped_column(String(255), nullable=False)
    index_prefix: Mapped[str] = mapped_column(String(128), nullable=False)
    index_version: Mapped[str] = mapped_column(String(128), nullable=False)
    projection_version: Mapped[str] = mapped_column(String(100), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="building")
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexing_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verification_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchIndexAuditModel(Base):
    __tablename__ = "search_index_audits"
    __table_args__ = (Index("ix_search_index_audits_created", "index_record_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    index_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    old_state: Mapped[str | None] = mapped_column(String(32))
    new_state: Mapped[str | None] = mapped_column(String(32))
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
