from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

def utcnow(): return datetime.now(timezone.utc)
def new_id(): return str(uuid4())

class AiCostRateModel(Base):
    __tablename__ = "ai_cost_rates"
    __table_args__ = (
        UniqueConstraint("provider", "model", "processing_mode", "effective_at", name="uq_ai_cost_rate_version"),
        CheckConstraint("input_unit_cost >= 0 AND output_unit_cost >= 0 AND media_unit_cost >= 0", name="ck_ai_cost_rates_nonnegative"),
        Index("ix_ai_cost_rates_resolve", "provider", "model", "effective_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    processing_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="any")
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_unit_cost: Mapped[float] = mapped_column(Numeric(24, 12), nullable=False, default=0)
    output_unit_cost: Mapped[float] = mapped_column(Numeric(24, 12), nullable=False, default=0)
    media_unit_cost: Mapped[float] = mapped_column(Numeric(24, 12), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

class TenantAiBudgetPolicyModel(Base):
    __tablename__ = "tenant_ai_budget_policies"
    __table_args__ = (
        CheckConstraint("daily_limit_micros IS NULL OR daily_limit_micros >= 0", name="ck_ai_budget_daily"),
        CheckConstraint("monthly_limit_micros IS NULL OR monthly_limit_micros >= 0", name="ck_ai_budget_monthly"),
        CheckConstraint("per_run_limit_micros IS NULL OR per_run_limit_micros >= 0", name="ck_ai_budget_run"),
        CheckConstraint("warning_threshold_percent >= 0 AND warning_threshold_percent <= 100", name="ck_ai_budget_warning"),
        CheckConstraint("hard_stop_threshold_percent > 0 AND hard_stop_threshold_percent <= 100", name="ck_ai_budget_hard"),
        CheckConstraint("warning_threshold_percent <= hard_stop_threshold_percent", name="ck_ai_budget_threshold_order"),
        CheckConstraint("action_on_limit IN ('defer', 'reject')", name="ck_ai_budget_action"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_limit_micros: Mapped[int | None] = mapped_column(BigInteger)
    monthly_limit_micros: Mapped[int | None] = mapped_column(BigInteger)
    per_run_limit_micros: Mapped[int | None] = mapped_column(BigInteger)
    warning_threshold_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    hard_stop_threshold_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    action_on_limit: Mapped[str] = mapped_column(String(20), nullable=False, default="defer")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

class AiBudgetAccountModel(Base):
    __tablename__ = "ai_budget_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_type", "period_key", "currency", name="uq_ai_budget_account_period"),
        CheckConstraint("period_type IN ('daily', 'monthly', 'pilot')", name="ck_ai_budget_account_type"),
        CheckConstraint("reserved_micros >= 0 AND actual_micros >= 0 AND limit_micros >= 0", name="ck_ai_budget_account_values"),
        Index("ix_ai_budget_accounts_tenant_period", "tenant_id", "period_type", "period_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    period_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period_key: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    limit_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    actual_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

class AiBudgetReservationModel(Base):
    __tablename__ = "ai_budget_reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "operation_key", name="uq_ai_budget_reservation_operation"),
        CheckConstraint("status IN ('reserved', 'reconciled', 'released', 'denied')", name="ck_ai_budget_reservation_status"),
        CheckConstraint("estimated_cost_micros >= 0 AND actual_cost_micros >= 0", name="ck_ai_budget_reservation_cost"),
        Index("ix_ai_budget_reservations_tenant_status", "tenant_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(512), nullable=False)
    analysis_id: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(String(36))
    pilot_run_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(255))
    processing_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="single")
    operation_item_id: Mapped[str | None] = mapped_column(String(255))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    estimated_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    actual_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="reserved")
    account_keys_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    denial_code: Mapped[str | None] = mapped_column(String(100))
    denial_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

class AiUsageRecordModel(Base):
    __tablename__ = "ai_usage_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_operation_key", name="uq_ai_usage_operation"),
        CheckConstraint("outcome IN ('completed', 'provider_failed', 'invalid_metadata', 'budget_blocked', 'cancelled')", name="ck_ai_usage_outcome"),
        Index("ix_ai_usage_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_ai_usage_analysis", "analysis_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_operation_key: Mapped[str] = mapped_column(String(512), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(36))
    analysis_id: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    processing_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="single")
    model: Mapped[str | None] = mapped_column(String(255))
    metadata_profile: Mapped[str | None] = mapped_column(String(255))
    metadata_profile_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    input_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    media_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_reported_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    locally_estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

class AiBudgetEventModel(Base):
    __tablename__ = "ai_budget_events"
    __table_args__ = (Index("ix_ai_budget_events_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

class AiRuntimeControlModel(Base):
    __tablename__ = "ai_runtime_controls"
    control_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    stopped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

class AiBudgetOverrideModel(Base):
    __tablename__ = "ai_budget_overrides"
    __table_args__ = (UniqueConstraint("tenant_id", "analysis_id", name="uq_ai_budget_override_analysis"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    analysis_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

class AiPilotRunModel(Base):
    __tablename__ = "ai_pilot_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ai_pilot_runs_tenant_id"),
        CheckConstraint("status IN ('pending', 'running', 'completed', 'cancelled', 'failed')", name="ck_ai_pilot_status"),
        Index("ix_ai_pilot_runs_tenant_status", "tenant_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    selection_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sample_seed: Mapped[str] = mapped_column(String(255), nullable=False, default="0")
    maximum_items: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_max_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class AiPilotItemModel(Base):
    __tablename__ = "ai_pilot_items"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "run_id"], ["ai_pilot_runs.tenant_id", "ai_pilot_runs.id"], ondelete="CASCADE", name="fk_ai_pilot_item_tenant_run"),
        UniqueConstraint("run_id", "asset_id", name="uq_ai_pilot_item_asset"),
        CheckConstraint("status IN ('pending', 'enqueued', 'completed', 'failed', 'budget_blocked', 'cancelled')", name="ck_ai_pilot_item_status"),
        Index("ix_ai_pilot_items_run_status", "run_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    analysis_id: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AiModelRateLimitStateModel(Base):
    """Shared start-rate state; primary key makes updates tenant/model atomic."""

    __tablename__ = "ai_model_rate_limit_state"
    __table_args__ = (
        Index(
            "ix_ai_model_rate_limit_next",
            "tenant_id",
            "provider",
            "model",
            "next_eligible_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(100), primary_key=True)
    model: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_eligible_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
