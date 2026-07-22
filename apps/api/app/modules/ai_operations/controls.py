from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_batch.model import AiBatchJobModel, BATCH_TERMINAL_STATUSES
from app.modules.ai_governance.repository import AiGovernanceRepository, MissingCostRateError
from app.modules.ai_operations.schema import AI_JOB_TYPES
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.repository import ProcessingPolicyRepository, policy_document
from app.modules.processing_policy.service import ProcessingPolicyService, TenantPolicyCache


class AiOperationsControlError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _job_document(job: ProcessingJobModel) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "provider": job.provider_key,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "cancellation_requested": job.cancellation_requested,
        "cancel_requested_at": (
            job.cancel_requested_at.isoformat() if job.cancel_requested_at else None
        ),
    }


class AiOperationsControlService:
    """Tenant-safe facade over existing policy, budget and job services."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        registry: AiProviderRegistry,
        cache: TenantPolicyCache | None = None,
    ):
        self.session = session
        self.settings = settings
        self.registry = registry
        self.policies = ProcessingPolicyRepository(session)
        self.policy_service = ProcessingPolicyService(self.policies, settings, cache)
        self.governance = AiGovernanceRepository(session)
        self.processing = ProcessingRepository(session)

    def pause_all(self, tenant_id: str, *, actor_id: str, reason: str) -> dict:
        value = self.policy_service.update(
            tenant_id, {"ai_analysis_enabled": False},
            actor_id=actor_id, reason=reason,
        )
        return policy_document(value)

    def resume_all(self, tenant_id: str, *, actor_id: str, reason: str) -> dict:
        value = self.policy_service.update(
            tenant_id, {"ai_analysis_enabled": True},
            actor_id=actor_id, reason=reason,
        )
        return policy_document(value)

    def set_provider_pause(
        self, tenant_id: str, provider: str, *, paused: bool,
        actor_id: str, reason: str,
    ) -> dict:
        self._provider(provider)
        value = self.policy_service.set_provider_pause(
            tenant_id, provider, "ai", paused=paused,
            actor_id=actor_id, reason=reason,
        )
        return policy_document(value)

    def update_defaults(
        self, tenant_id: str, *, provider: str, model: str,
        actor_id: str, reason: str,
    ) -> dict:
        self._provider(provider)
        self._validate_model(provider, model)
        policy = self.policies.get_provider(tenant_id, provider, "ai")
        modes = []
        if policy is None or policy.single_enabled:
            modes.append("single")
        if policy is None or policy.batch_enabled:
            modes.append("batch")
        adapter = self.registry.require(provider)
        modes = [
            mode for mode in modes
            if (mode == "single" and adapter.supports_single)
            or (mode == "batch" and adapter.supports_batch)
        ]
        if not modes:
            raise AiOperationsControlError(
                "ai_provider_unavailable", "No enabled processing mode is available.",
                status_code=503,
            )
        for mode in modes:
            self._require_cost_rate(provider, model, mode)
        value = self.policy_service.update(
            tenant_id,
            {"default_ai_provider": provider, "default_ai_model": model},
            actor_id=actor_id, reason=reason,
        )
        return policy_document(value)

    def update_provider_controls(
        self, tenant_id: str, provider: str, changes: Mapping[str, Any],
        *, actor_id: str, reason: str,
    ) -> dict:
        adapter = self._provider(provider)
        provider_changes = {
            key: value for key, value in changes.items()
            if key != "tenant_ai_active_jobs_limit"
        }
        model = self._tenant_default_model(tenant_id, provider)
        if provider_changes.get("single_enabled"):
            if not adapter.supports_single:
                raise AiOperationsControlError("ai_mode_unavailable", "Single mode is unsupported.")
            self._require_cost_rate(provider, model, "single")
        if provider_changes.get("batch_enabled"):
            if not adapter.supports_batch:
                raise AiOperationsControlError("ai_mode_unavailable", "Batch mode is unsupported.")
            self._require_cost_rate(provider, model, "batch")
        if provider_changes:
            value = self.policy_service.update_provider(
                tenant_id, provider, "ai", provider_changes,
                actor_id=actor_id, reason=reason,
            )
        else:
            value = self.policies.get_or_create_provider(tenant_id, provider, "ai")
        tenant_limit = changes.get("tenant_ai_active_jobs_limit")
        if tenant_limit is not None:
            self.policy_service.update(
                tenant_id, {"ai_active_jobs_limit": tenant_limit},
                actor_id=actor_id, reason=reason,
            )
        return policy_document(value)

    def update_budget(
        self, tenant_id: str, changes: Mapping[str, Any],
        *, actor_id: str, reason: str,
    ) -> dict:
        before = self.governance.get_policy(tenant_id)
        old = self._budget_document(before)
        value = self.governance.upsert_policy(tenant_id, changes)
        new = self._budget_document(value)
        self.governance.event(
            tenant_id, "budget_policy_updated", actor_id=actor_id, reason=reason,
            details={"old_policy": old, "new_policy": new},
        )
        self.policies.audit(
            actor_id=actor_id, tenant_id=tenant_id,
            action="ai_budget_updated", old_policy=old or {}, new_policy=new or {},
            reason=reason,
        )
        return new or {}

    def retry_job(
        self, tenant_id: str, job_id: str, *, actor_id: str, reason: str,
    ) -> tuple[dict, str]:
        job = self._locked_job(tenant_id, job_id)
        if job.job_type not in AI_JOB_TYPES:
            raise AiOperationsControlError("job_not_ai", "Only AI jobs can be retried.")
        if job.status == "retry" and job.last_error_code == "operator_retry_requested":
            return _job_document(job), "already_requested"
        if job.status != "failed" or job.last_error_code in {
            "operation_cancelled", "analysis_cancelled", "batch_cancelled",
        }:
            raise AiOperationsControlError(
                "job_not_retryable", "The job is not eligible for retry.", status_code=409,
            )
        before = _job_document(job)
        job.status = "retry"
        job.next_attempt_at = datetime.now(timezone.utc)
        job.completed_at = None
        job.claimed_by = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.cancellation_requested = False
        job.cancel_requested_at = None
        job.cancel_requested_by = None
        job.cancellation_reason = None
        job.max_attempts = max(job.max_attempts, job.attempt_count + 1)
        job.last_error_code = "operator_retry_requested"
        job.last_error_message = "Retry requested by an authorized operator."
        job.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        after = _job_document(job)
        self._audit_job(tenant_id, actor_id, reason, "ai_job_retry_requested", before, after, job)
        return after, "retry_requested"

    def cancel_job(
        self, tenant_id: str, job_id: str, *, actor_id: str, reason: str,
    ) -> tuple[dict, str]:
        job = self._locked_job(tenant_id, job_id)
        if job.job_type not in AI_JOB_TYPES:
            raise AiOperationsControlError("job_not_ai", "Only AI jobs can be cancelled.")
        batch = self._batch_for_job(tenant_id, job)
        if job.cancellation_requested or job.last_error_code == "operation_cancelled":
            return _job_document(job), self._cancel_outcome(job, batch)
        if job.status not in {"pending", "retry", "processing"}:
            raise AiOperationsControlError(
                "job_not_cancellable", "The job is not eligible for cancellation.",
                status_code=409,
            )
        before = _job_document(job)
        now = datetime.now(timezone.utc)
        job.cancellation_requested = True
        job.cancel_requested_at = now
        job.cancel_requested_by = actor_id
        job.cancellation_reason = reason
        if batch is not None and batch.status not in BATCH_TERMINAL_STATUSES:
            batch.cancellation_requested = True
        if job.status in {"pending", "retry"}:
            self.processing.cancel_unstarted_job(
                tenant_id=tenant_id, job_id=job.id,
                actor_id=actor_id, reason=reason, now=now,
            )
        self.session.flush()
        after = _job_document(job)
        outcome = self._cancel_outcome(job, batch)
        self._audit_job(tenant_id, actor_id, reason, "ai_job_cancel_requested", before, after, job)
        return after, outcome

    def _provider(self, provider: str):
        if provider not in {"gemini", "openai"}:
            raise AiOperationsControlError("invalid_provider", "Unsupported AI provider.")
        adapter = self.registry.get(provider)
        if adapter is None:
            raise AiOperationsControlError(
                "ai_provider_unavailable", "The AI provider is not configured.",
                status_code=503,
            )
        return adapter

    def _validate_model(self, provider: str, model: str) -> None:
        allowed = (
            self.settings.gemini_allowed_models if provider == "gemini"
            else self.settings.openai_allowed_models
        )
        if model not in allowed:
            raise AiOperationsControlError(
                "ai_model_not_allowed", "The requested AI model is not allowed."
            )

    def _tenant_default_model(self, tenant_id: str, provider: str) -> str:
        tenant = self.policies.get_tenant(tenant_id)
        if tenant and tenant.default_ai_provider == provider and tenant.default_ai_model:
            model = tenant.default_ai_model
        else:
            model = (
                self.settings.GEMINI_MODEL if provider == "gemini"
                else self.settings.OPENAI_DEFAULT_MODEL
            )
        self._validate_model(provider, model)
        return model

    def _require_cost_rate(self, provider: str, model: str, mode: str) -> None:
        try:
            self.governance.require_cost_rate(provider, model, mode)
        except MissingCostRateError as exc:
            raise AiOperationsControlError(
                "missing_cost_rate", "No cost rate is configured for the selected provider/model/mode.",
                status_code=409,
            ) from exc

    def _locked_job(self, tenant_id: str, job_id: str) -> ProcessingJobModel:
        job = self.session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.id == job_id,
            ).with_for_update()
        )
        if job is None:
            raise AiOperationsControlError("job_not_found", "Job not found.", status_code=404)
        return job

    def _batch_for_job(self, tenant_id: str, job: ProcessingJobModel) -> AiBatchJobModel | None:
        if job.entity_type != "ai_batch_job":
            return None
        return self.session.scalar(select(AiBatchJobModel).where(
            AiBatchJobModel.tenant_id == tenant_id,
            AiBatchJobModel.id == job.entity_id,
        ))

    @staticmethod
    def _cancel_outcome(job: ProcessingJobModel, batch: AiBatchJobModel | None = None) -> str:
        if batch is not None and batch.provider_batch_id:
            return "provider_batch_cancel_requested"
        if job.status == "processing":
            return "running_cancel_requested"
        return "queued_cancelled"

    def _audit_job(
        self, tenant_id: str, actor_id: str, reason: str, action: str,
        before: Mapping[str, Any], after: Mapping[str, Any], job: ProcessingJobModel,
    ) -> None:
        self.policies.audit(
            actor_id=actor_id, tenant_id=tenant_id, action=action,
            old_policy=before, new_policy=after, reason=reason,
            provider_key=job.provider_key, provider_scope="ai",
        )

    @staticmethod
    def _budget_document(value) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "enabled": value.enabled,
            "daily_limit_micros": value.daily_limit_micros,
            "monthly_limit_micros": value.monthly_limit_micros,
            "currency": value.currency,
            "updated_at": value.updated_at.isoformat(),
        }
