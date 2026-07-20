from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Any, Mapping

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.ai_governance.model import *

def micros(value: Decimal | float | int | str) -> int:
    return int((Decimal(str(value)) * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))

class AiGovernanceRepository:
    def __init__(self, session: Session): self.session = session

    def resolve_cost_rate(self, provider: str, model: str, at: datetime | None = None):
        at = at or datetime.now(timezone.utc)
        return self.session.scalar(select(AiCostRateModel).where(
            AiCostRateModel.provider == provider, AiCostRateModel.model == model, AiCostRateModel.effective_at <= at,
        ).order_by(AiCostRateModel.effective_at.desc()).limit(1))

    def estimate_cost(self, rate, input_units: int, output_units: int, media_units: int) -> int:
        if rate is None: return 0
        value = Decimal(input_units) * Decimal(rate.input_unit_cost) + Decimal(output_units) * Decimal(rate.output_unit_cost) + Decimal(media_units) * Decimal(rate.media_unit_cost)
        return micros(value)

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
