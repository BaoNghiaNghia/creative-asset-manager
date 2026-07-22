from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.ai_governance.model import *
from app.modules.processing_policy.model import TenantProviderPolicyModel

def micros(value: Decimal | float | int | str) -> int:
    return int((Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))

class MissingCostRateError(RuntimeError):
    code = "missing_cost_rate"
    def __init__(self, provider: str, model: str, processing_mode: str):
        super().__init__(f"No cost rate is configured for {provider}/{model}/{processing_mode}.")
        self.provider = provider
        self.model = model
        self.processing_mode = processing_mode

class ProviderGovernanceBlocked(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason

class AiGovernanceRepository:
    def __init__(self, session: Session): self.session = session

    def resolve_cost_rate(self, provider: str, model: str, at: datetime | None = None,
                          processing_mode: str = "single"):
        at = at or datetime.now(timezone.utc)
        for mode in (processing_mode, "any"):
            rate = self.session.scalar(select(AiCostRateModel).where(
                AiCostRateModel.provider == provider,
                AiCostRateModel.model == model,
                AiCostRateModel.processing_mode == mode,
                AiCostRateModel.effective_at <= at,
            ).order_by(AiCostRateModel.effective_at.desc()).limit(1))
            if rate is not None:
                return rate
        return None

    def require_cost_rate(self, provider: str, model: str, processing_mode: str,
                          at: datetime | None = None):
        rate = self.resolve_cost_rate(provider, model, at, processing_mode)
        if rate is None:
            raise MissingCostRateError(provider, model, processing_mode)
        return rate

    def estimate_cost(self, rate, input_units: int, output_units: int, media_units: int) -> int:
        if rate is None:
            raise MissingCostRateError("unknown", "unknown", "single")
        value = Decimal(input_units) * Decimal(rate.input_unit_cost) + Decimal(output_units) * Decimal(rate.output_unit_cost) + Decimal(media_units) * Decimal(rate.media_unit_cost)
        return micros(value)

    def provider_policy(self, tenant_id: str, provider: str, *, for_update: bool = False):
        statement = select(TenantProviderPolicyModel).where(
            TenantProviderPolicyModel.tenant_id == tenant_id,
            TenantProviderPolicyModel.provider_key == provider,
            TenantProviderPolicyModel.provider_scope == "ai",
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def runtime_stopped(self, provider: str) -> tuple[bool, str | None]:
        controls = list(self.session.scalars(select(AiRuntimeControlModel).where(
            AiRuntimeControlModel.control_key.in_(("global", provider)))))
        stopped = [value for value in controls if value.stopped]
        return (bool(stopped), stopped[0].reason if stopped else None)

    def assert_provider_allowed(self, tenant_id: str, provider: str, processing_mode: str) -> None:
        stopped, reason = self.runtime_stopped(provider)
        if stopped:
            raise ProviderGovernanceBlocked("ai_emergency_stop", reason or "AI is stopped by an operator.")
        policy = self.provider_policy(tenant_id, provider)
        if policy is None:
            return
        if not policy.processing_enabled:
            raise ProviderGovernanceBlocked("ai_provider_disabled", "AI provider is disabled for this tenant.")
        if policy.processing_paused or policy.emergency_stop:
            raise ProviderGovernanceBlocked("ai_provider_paused", policy.pause_reason or "AI provider is paused.")
        if processing_mode == "single" and not policy.single_enabled:
            raise ProviderGovernanceBlocked("ai_provider_mode_disabled", "Single analysis is disabled for this provider.")
        if processing_mode == "batch" and not policy.batch_enabled:
            raise ProviderGovernanceBlocked("ai_provider_mode_disabled", "Batch analysis is disabled for this provider.")

    def has_budget_override(self, tenant_id: str, analysis_id: str) -> bool:
        return self.session.scalar(select(AiBudgetOverrideModel.id).where(
            AiBudgetOverrideModel.tenant_id == tenant_id,
            AiBudgetOverrideModel.analysis_id == analysis_id,
            AiBudgetOverrideModel.active.is_(True),
        )) is not None

    def grant_budget_override(self, tenant_id: str, analysis_id: str, actor_id: str, reason: str):
        value = self.session.scalar(select(AiBudgetOverrideModel).where(
            AiBudgetOverrideModel.tenant_id == tenant_id,
            AiBudgetOverrideModel.analysis_id == analysis_id))
        if value is None:
            value = AiBudgetOverrideModel(tenant_id=tenant_id, analysis_id=analysis_id,
                                          actor_id=actor_id, reason=reason)
            self.session.add(value)
        else:
            value.active = True
            value.actor_id = actor_id
            value.reason = reason
        self.event(tenant_id, "budget_override", reason=reason,
                   details={"analysis_id": analysis_id}, actor_id=actor_id)
        self.session.flush()
        return value

    def set_runtime_stop(self, control_key: str, stopped: bool, actor_id: str, reason: str | None):
        if control_key not in {"global", "gemini", "openai"}:
            raise ValueError("unsupported AI runtime control")
        value = self.session.get(AiRuntimeControlModel, control_key)
        if value is None:
            value = AiRuntimeControlModel(control_key=control_key)
            self.session.add(value)
        value.stopped = stopped
        value.reason = reason
        value.updated_by = actor_id
        value.updated_at = datetime.now(timezone.utc)
        self.event("platform", "ai_emergency_stop" if stopped else "ai_emergency_resume",
                   reason=reason, details={"control_key": control_key}, actor_id=actor_id)
        self.session.flush()
        return value

    def get_policy(self, tenant_id: str, *, for_update: bool = False):
        statement = select(TenantAiBudgetPolicyModel).where(TenantAiBudgetPolicyModel.tenant_id == tenant_id)
        if for_update and self.session.get_bind().dialect.name == "postgresql": statement = statement.with_for_update()
        return self.session.scalar(statement)

    def upsert_policy(self, tenant_id: str, values: Mapping[str, Any]):
        policy = self.get_policy(tenant_id)
        if policy is None:
            policy = TenantAiBudgetPolicyModel(tenant_id=tenant_id); self.session.add(policy); self.session.flush()
        allowed = {"enabled", "daily_limit_micros", "monthly_limit_micros", "per_run_limit_micros", "warning_threshold_percent", "hard_stop_threshold_percent", "timezone", "currency", "action_on_limit"}
        if set(values) - allowed: raise ValueError("unsupported budget policy field")
        for key,value in values.items(): setattr(policy,key,value)
        policy.updated_at = datetime.now(timezone.utc); self.session.flush(); return policy

    def account(self, tenant_id: str, period_type: str, period_key: str, limit_micros: int, currency: str = "USD"):
        value = self.session.scalar(select(AiBudgetAccountModel).where(
            AiBudgetAccountModel.tenant_id == tenant_id, AiBudgetAccountModel.period_type == period_type, AiBudgetAccountModel.period_key == period_key, AiBudgetAccountModel.currency == currency))
        if value is not None:
            if value.limit_micros != limit_micros: value.limit_micros = limit_micros; self.session.flush()
            return value
        try:
            with self.session.begin_nested():
                value = AiBudgetAccountModel(tenant_id=tenant_id, period_type=period_type, period_key=period_key, currency=currency, limit_micros=limit_micros)
                self.session.add(value); self.session.flush()
            return value
        except IntegrityError:
            return self.session.scalar(select(AiBudgetAccountModel).where(
                AiBudgetAccountModel.tenant_id == tenant_id, AiBudgetAccountModel.period_type == period_type, AiBudgetAccountModel.period_key == period_key, AiBudgetAccountModel.currency == currency))

    def reservation(self, tenant_id: str, operation_key: str):
        return self.session.scalar(select(AiBudgetReservationModel).where(AiBudgetReservationModel.tenant_id == tenant_id, AiBudgetReservationModel.operation_key == operation_key))

    def record_usage(self, *, tenant_id: str, operation_key: str, values: Mapping[str, Any]):
        usage = self.session.scalar(select(AiUsageRecordModel).where(AiUsageRecordModel.tenant_id == tenant_id, AiUsageRecordModel.provider_operation_key == operation_key))
        if usage is None:
            usage = AiUsageRecordModel(tenant_id=tenant_id, provider_operation_key=operation_key, **dict(values)); self.session.add(usage)
        else:
            for key,value in values.items(): setattr(usage,key,value)
        self.session.flush(); return usage

    def event(self, tenant_id: str, action: str, reason: str | None = None, details: Mapping[str, Any] | None = None, actor_id: str | None = None):
        event = AiBudgetEventModel(tenant_id=tenant_id, action=action, reason=reason, details_json=dict(details or {}), actor_id=actor_id)
        self.session.add(event); self.session.flush(); return event
