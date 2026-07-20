from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.model import AiBudgetAccountModel, AiBudgetReservationModel
from app.modules.ai_governance.repository import AiGovernanceRepository

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

    def reserve(self, *, tenant_id: str, operation_key: str, estimated_cost_micros: int, analysis_id: str | None = None, job_id: str | None = None, pilot_run_id: str | None = None, currency: str = "USD", now: datetime | None = None) -> BudgetDecision:
        now = now or datetime.now(timezone.utc)
        existing = self.repository.reservation(tenant_id, operation_key)
        if existing is not None:
            return BudgetDecision(existing.status != "denied", existing.id, existing.estimated_cost_micros, existing.denial_code, existing.denial_reason)
        if self.settings.AI_EMERGENCY_STOP_ENABLED:
            return self._deny(tenant_id, operation_key, estimated_cost_micros, "global_ai_stop", "Global emergency AI stop is enabled.", "defer", analysis_id, job_id, pilot_run_id, currency)
        policy = self.repository.get_policy(tenant_id, for_update=True)
        if policy is not None and policy.enabled and policy.currency != currency:
            return self._deny(
                tenant_id, operation_key, estimated_cost_micros,
                "budget_currency_mismatch",
                f"AI cost currency {currency} does not match tenant budget currency {policy.currency}.",
                policy.action_on_limit, analysis_id, job_id, pilot_run_id, currency,
            )
        if policy is None or not policy.enabled:
            reservation = AiBudgetReservationModel(tenant_id=tenant_id, operation_key=operation_key, analysis_id=analysis_id, job_id=job_id, pilot_run_id=pilot_run_id, estimated_cost_micros=estimated_cost_micros, currency=currency, account_keys_json=[])
            self.session.add(reservation); self.session.flush(); AI_METRICS.increment("budget_reservations", provider="gemini", outcome="unlimited")
            return BudgetDecision(True, reservation.id, estimated_cost_micros)
        account_specs=[]
        if policy.daily_limit_micros is not None: account_specs.append(("daily", now.astimezone(timezone.utc).date().isoformat(), policy.daily_limit_micros))
        if policy.monthly_limit_micros is not None: account_specs.append(("monthly", now.astimezone(timezone.utc).strftime("%Y-%m"), policy.monthly_limit_micros))
        if pilot_run_id and policy.per_run_limit_micros is not None: account_specs.append(("pilot", pilot_run_id, policy.per_run_limit_micros))
        account_keys=[]
        try:
            with self.session.begin_nested():
                reservation = AiBudgetReservationModel(tenant_id=tenant_id, operation_key=operation_key, analysis_id=analysis_id, job_id=job_id, pilot_run_id=pilot_run_id, estimated_cost_micros=estimated_cost_micros, currency=currency, account_keys_json=[])
                self.session.add(reservation); self.session.flush()
                for period_type,period_key,limit in account_specs:
                    account=self.repository.account(tenant_id,period_type,period_key,limit,currency)
                    hard_limit=(limit * policy.hard_stop_threshold_percent)//100
                    reserved=self.session.scalar(update(AiBudgetAccountModel).where(
                        AiBudgetAccountModel.id==account.id,
                        AiBudgetAccountModel.actual_micros + AiBudgetAccountModel.reserved_micros + estimated_cost_micros <= hard_limit,
                    ).values(reserved_micros=AiBudgetAccountModel.reserved_micros+estimated_cost_micros, updated_at=now).returning(AiBudgetAccountModel.id).execution_options(synchronize_session=False))
                    if reserved is None: raise BudgetDenied(f"{period_type}_budget_exceeded", f"The {period_type} AI budget would be exceeded.", policy.action_on_limit)
                    account_keys.append(f"{currency}:{period_type}:{period_key}")
                    projected=account.actual_micros + account.reserved_micros + estimated_cost_micros
                    if limit and projected * 100 >= limit * policy.warning_threshold_percent:
                        self.repository.event(tenant_id,"budget_warning",details={"period_type":period_type,"period_key":period_key,"projected_micros":projected,"limit_micros":limit})
                reservation.account_keys_json=account_keys; self.session.flush()
            AI_METRICS.increment("budget_reservations",provider="gemini",outcome="reserved")
            return BudgetDecision(True,reservation.id,estimated_cost_micros)
        except BudgetDenied as exc:
            return self._deny(tenant_id,operation_key,estimated_cost_micros,exc.code,exc.reason,exc.action,analysis_id,job_id,pilot_run_id,currency)
        except IntegrityError:
            existing=self.repository.reservation(tenant_id,operation_key)
            if existing is None: raise
            return BudgetDecision(existing.status != "denied",existing.id,existing.estimated_cost_micros,existing.denial_code,existing.denial_reason)

    def _deny(self, tenant_id, operation_key, estimate, code, reason, action, analysis_id, job_id, pilot_run_id, currency):
        existing=self.repository.reservation(tenant_id,operation_key)
        if existing is None:
            existing=AiBudgetReservationModel(tenant_id=tenant_id,operation_key=operation_key,analysis_id=analysis_id,job_id=job_id,pilot_run_id=pilot_run_id,estimated_cost_micros=estimate,currency=currency,status="denied",denial_code=code,denial_reason=reason,account_keys_json=[])
            self.session.add(existing); self.session.flush()
            self.repository.event(tenant_id,"budget_denied",reason=reason,details={"code":code,"estimated_cost_micros":estimate})
        AI_METRICS.increment("budget_denials",provider="gemini",outcome=code); AI_METRICS.increment("breaker_state",provider="gemini",outcome="open")
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
