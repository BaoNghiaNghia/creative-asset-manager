from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.model import AiBudgetAccountModel, AiBudgetReservationModel
from app.modules.ai_governance.repository import AiGovernanceRepository, ProviderGovernanceBlocked

@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reservation_id: str | None
    estimated_cost_micros: int
    code: str | None = None
    reason: str | None = None
    action: str = "defer"

class BudgetDenied(RuntimeError):
    def __init__(self, code: str, reason: str, action: str):
        super().__init__(reason); self.code=code; self.reason=reason; self.action=action

class AiBudgetService:
    def __init__(self, repository: AiGovernanceRepository, settings: Settings):
        self.repository=repository; self.session=repository.session; self.settings=settings

    def reserve(self, *, tenant_id: str, operation_key: str, estimated_cost_micros: int,
                analysis_id: str | None = None, job_id: str | None = None,
                pilot_run_id: str | None = None, currency: str = "USD",
                provider: str = "unknown", model: str | None = None,
                processing_mode: str = "single", operation_item_id: str | None = None,
                attempt_number: int = 1, now: datetime | None = None) -> BudgetDecision:
        now = now or datetime.now(timezone.utc)
        existing = self.repository.reservation(tenant_id, operation_key)
        if existing is not None:
            return BudgetDecision(existing.status != "denied", existing.id, existing.estimated_cost_micros, existing.denial_code, existing.denial_reason)
        stopped, stop_reason = self.repository.runtime_stopped(provider)
        provider_stop = bool(getattr(self.settings, f"{provider.upper()}_EMERGENCY_STOP_ENABLED", False))
        if self.settings.AI_EMERGENCY_STOP_ENABLED or provider_stop or stopped:
            return self._deny(tenant_id, operation_key, estimated_cost_micros, "global_ai_stop" if self.settings.AI_EMERGENCY_STOP_ENABLED else "ai_emergency_stop",
                              stop_reason or "AI emergency stop is enabled.", "defer",
                              analysis_id, job_id, pilot_run_id, currency, provider, model,
                              processing_mode, operation_item_id, attempt_number)
        try:
            self.repository.assert_provider_allowed(tenant_id, provider, processing_mode)
        except ProviderGovernanceBlocked as exc:
            return self._deny(tenant_id, operation_key, estimated_cost_micros, exc.code, exc.reason,
                              "defer", analysis_id, job_id, pilot_run_id, currency, provider,
                              model, processing_mode, operation_item_id, attempt_number)

        policy = self.repository.get_policy(tenant_id, for_update=True)
        provider_policy = self.repository.provider_policy(tenant_id, provider, for_update=True)
        if policy is not None and policy.enabled and policy.currency != currency:
            return self._deny(tenant_id, operation_key, estimated_cost_micros,
                              "budget_currency_mismatch",
                              f"AI cost currency {currency} does not match tenant budget currency {policy.currency}.",
                              policy.action_on_limit, analysis_id, job_id, pilot_run_id, currency,
                              provider, model, processing_mode, operation_item_id, attempt_number)

        account_specs: list[tuple[str,str,int]] = []
        if provider_policy is not None:
            if provider_policy.budget_currency != currency and (
                provider_policy.daily_budget_limit_micros is not None or
                provider_policy.monthly_budget_limit_micros is not None
            ):
                return self._deny(tenant_id, operation_key, estimated_cost_micros,
                                  "budget_currency_mismatch", "Provider budget currency mismatch.",
                                  "defer", analysis_id, job_id, pilot_run_id, currency,
                                  provider, model, processing_mode, operation_item_id, attempt_number)
            if provider_policy.daily_budget_limit_micros is not None:
                account_specs.append(("daily", f"{provider}:{now.date().isoformat()}",
                                      provider_policy.daily_budget_limit_micros))
            if provider_policy.monthly_budget_limit_micros is not None:
                account_specs.append(("monthly", f"{provider}:{now.strftime('%Y-%m')}",
                                      provider_policy.monthly_budget_limit_micros))
        if policy is not None and policy.enabled:
            if policy.daily_limit_micros is not None:
                account_specs.append(("daily", now.date().isoformat(), policy.daily_limit_micros))
            if policy.monthly_limit_micros is not None:
                account_specs.append(("monthly", now.strftime("%Y-%m"), policy.monthly_limit_micros))
            if pilot_run_id and policy.per_run_limit_micros is not None:
                account_specs.append(("pilot", pilot_run_id, policy.per_run_limit_micros))

        reservation_values = dict(
            tenant_id=tenant_id, operation_key=operation_key, analysis_id=analysis_id,
            job_id=job_id, pilot_run_id=pilot_run_id,
            estimated_cost_micros=estimated_cost_micros, currency=currency,
            provider=provider, model=model, processing_mode=processing_mode,
            operation_item_id=operation_item_id, attempt_number=attempt_number,
            account_keys_json=[],
        )
        if not account_specs:
            reservation = AiBudgetReservationModel(**reservation_values)
            self.session.add(reservation); self.session.flush()
            AI_METRICS.increment("budget_reservations", provider=provider, mode=processing_mode, outcome="unlimited")
            return BudgetDecision(True, reservation.id, estimated_cost_micros)

        account_keys=[]
        warning = policy.warning_threshold_percent if policy and policy.enabled else 80
        hard_stop = policy.hard_stop_threshold_percent if policy and policy.enabled else 100
        action = policy.action_on_limit if policy and policy.enabled else "defer"
        try:
            with self.session.begin_nested():
                reservation = AiBudgetReservationModel(**reservation_values)
                self.session.add(reservation); self.session.flush()
                for period_type,period_key,limit in account_specs:
                    account=self.repository.account(tenant_id,period_type,period_key,limit,currency)
                    hard_limit=(limit * hard_stop)//100
                    reserved=self.session.scalar(update(AiBudgetAccountModel).where(
                        AiBudgetAccountModel.id==account.id,
                        AiBudgetAccountModel.actual_micros + AiBudgetAccountModel.reserved_micros + estimated_cost_micros <= hard_limit,
                    ).values(reserved_micros=AiBudgetAccountModel.reserved_micros+estimated_cost_micros, updated_at=now).returning(AiBudgetAccountModel.id).execution_options(synchronize_session=False))
                    if reserved is None:
                        raise BudgetDenied(f"{period_type}_budget_exceeded", f"The {period_type} AI budget would be exceeded.", action)
                    account_keys.append(f"{currency}:{period_type}:{period_key}")
                    projected=account.actual_micros + account.reserved_micros + estimated_cost_micros
                    if limit and projected * 100 >= limit * warning:
                        self.repository.event(tenant_id,"budget_warning",details={"provider":provider,"mode":processing_mode,"period_type":period_type,"period_key":period_key,"projected_micros":projected,"limit_micros":limit})
                reservation.account_keys_json=account_keys; self.session.flush()
            AI_METRICS.increment("budget_reservations",provider=provider,mode=processing_mode,outcome="reserved")
            return BudgetDecision(True,reservation.id,estimated_cost_micros)
        except BudgetDenied as exc:
            return self._deny(tenant_id,operation_key,estimated_cost_micros,exc.code,exc.reason,
                              exc.action,analysis_id,job_id,pilot_run_id,currency,provider,model,
                              processing_mode,operation_item_id,attempt_number)
        except IntegrityError:
            existing=self.repository.reservation(tenant_id,operation_key)
            if existing is None: raise
            return BudgetDecision(existing.status != "denied",existing.id,existing.estimated_cost_micros,existing.denial_code,existing.denial_reason)

    def _deny(self, tenant_id, operation_key, estimate, code, reason, action, analysis_id,
              job_id, pilot_run_id, currency, provider, model, processing_mode,
              operation_item_id, attempt_number):
        existing=self.repository.reservation(tenant_id,operation_key)
        if existing is None:
            existing=AiBudgetReservationModel(
                tenant_id=tenant_id,operation_key=operation_key,analysis_id=analysis_id,
                job_id=job_id,pilot_run_id=pilot_run_id,estimated_cost_micros=estimate,
                currency=currency,status="denied",denial_code=code,denial_reason=reason,
                provider=provider,model=model,processing_mode=processing_mode,
                operation_item_id=operation_item_id,attempt_number=attempt_number,
                account_keys_json=[])
            self.session.add(existing); self.session.flush()
            self.repository.event(tenant_id,"budget_denied",reason=reason,
                                  details={"code":code,"provider":provider,"mode":processing_mode,
                                           "estimated_cost_micros":estimate})
        AI_METRICS.increment("budget_blocks",provider=provider,mode=processing_mode,outcome=code)
        AI_METRICS.increment("breaker_state",provider=provider,mode=processing_mode,outcome="open")
        return BudgetDecision(False,existing.id,estimate,code,reason,action)

    def reconcile(self, reservation_id: str, actual_cost_micros: int, *, now: datetime | None = None):
        now=now or datetime.now(timezone.utc)
        reservation=self.session.scalar(select(AiBudgetReservationModel).where(AiBudgetReservationModel.id==reservation_id))
        if reservation is None: raise LookupError(reservation_id)
        if reservation.status in {"released","denied"}: return reservation
        previous = reservation.actual_cost_micros if reservation.status == "reconciled" else 0
        reserved_release = 0 if reservation.status == "reconciled" else reservation.estimated_cost_micros
        actual_delta = actual_cost_micros - previous
        for value in reservation.account_keys_json:
            currency,period_type,period_key=value.split(":",2)
            self.session.execute(update(AiBudgetAccountModel).where(
                AiBudgetAccountModel.tenant_id==reservation.tenant_id,AiBudgetAccountModel.period_type==period_type,AiBudgetAccountModel.period_key==period_key,AiBudgetAccountModel.currency==currency,
            ).values(reserved_micros=AiBudgetAccountModel.reserved_micros-reserved_release,actual_micros=AiBudgetAccountModel.actual_micros+actual_delta,updated_at=now).execution_options(synchronize_session=False))
        reservation.actual_cost_micros=actual_cost_micros; reservation.status="reconciled"; reservation.updated_at=now; self.session.flush(); return reservation

def usage_units(usage: Mapping[str, Any]) -> tuple[int,int,int]:
    def value(*keys):
        for key in keys:
            if key in usage:
                try: return max(0,int(usage[key] or 0))
                except (TypeError,ValueError): return 0
        return 0
    return value("promptTokenCount","input_tokens","input"), value("candidatesTokenCount","output_tokens","output"), value("imageCount","media_units","images")
