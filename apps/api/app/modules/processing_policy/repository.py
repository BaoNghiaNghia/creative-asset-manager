from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import (
    ProcessingPolicyAuditModel,
    TenantProcessingPolicyModel,
    TenantProviderPolicyModel,
)


TENANT_EDITABLE_FIELDS = {
    "pipeline_enabled", "source_sync_enabled", "download_enabled",
    "managed_storage_enabled", "ai_analysis_enabled", "search_v2_enabled",
    "sidecar_enabled", "rollout_mode", "rollout_percentage",
    "total_active_jobs_limit", "ai_active_jobs_limit",
    "source_active_jobs_limit", "storage_active_jobs_limit",
    "default_ai_provider", "default_ai_model",
    "default_ai_mode", "default_metadata_profile", "auto_analyze_new_assets",
    "daily_ai_item_limit", "ai_retry_count", "ai_timeout_seconds",
}


def policy_document(policy: TenantProcessingPolicyModel | TenantProviderPolicyModel) -> dict[str, Any]:
    hidden = {"_sa_instance_state"}
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in policy.__dict__.items()
        if key not in hidden
    }


class ProcessingPolicyRepository:
    """Authoritative policy persistence; methods flush and never commit."""

    def __init__(self, session: Session):
        self.session = session

    def get_tenant(self, tenant_id: str, *, for_update: bool = False) -> TenantProcessingPolicyModel | None:
        statement = select(TenantProcessingPolicyModel).where(
            TenantProcessingPolicyModel.tenant_id == tenant_id
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_or_create_tenant(self, tenant_id: str) -> TenantProcessingPolicyModel:
        policy = self.get_tenant(tenant_id)
        if policy is None:
            policy = TenantProcessingPolicyModel(tenant_id=tenant_id)
            self.session.add(policy)
            self.session.flush()
        return policy

    def update_tenant(self, tenant_id: str, changes: Mapping[str, Any]) -> TenantProcessingPolicyModel:
        unknown = set(changes) - TENANT_EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"unsupported policy fields: {sorted(unknown)}")
        policy = self.get_or_create_tenant(tenant_id)
        for key, value in changes.items():
            setattr(policy, key, value)
        policy.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return policy

    def pause_tenant(self, tenant_id: str, *, actor_id: str, reason: str) -> TenantProcessingPolicyModel:
        if not reason.strip():
            raise ValueError("pause reason is required")
        policy = self.get_or_create_tenant(tenant_id)
        policy.processing_paused = True
        policy.pause_reason = reason.strip()
        policy.paused_by = actor_id
        policy.paused_at = datetime.now(timezone.utc)
        policy.updated_at = policy.paused_at
        self.session.flush()
        return policy

    def resume_tenant(self, tenant_id: str) -> TenantProcessingPolicyModel:
        policy = self.get_or_create_tenant(tenant_id)
        policy.processing_paused = False
        policy.pause_reason = None
        policy.paused_by = None
        policy.paused_at = None
        policy.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return policy

    def get_provider(self, tenant_id: str, provider_key: str, provider_scope: str) -> TenantProviderPolicyModel | None:
        return self.session.scalar(select(TenantProviderPolicyModel).where(
            TenantProviderPolicyModel.tenant_id == tenant_id,
            TenantProviderPolicyModel.provider_key == provider_key,
            TenantProviderPolicyModel.provider_scope == provider_scope,
        ))

    def get_or_create_provider(self, tenant_id: str, provider_key: str, provider_scope: str) -> TenantProviderPolicyModel:
        policy = self.get_provider(tenant_id, provider_key, provider_scope)
        if policy is None:
            policy = TenantProviderPolicyModel(
                tenant_id=tenant_id, provider_key=provider_key,
                provider_scope=provider_scope,
            )
            self.session.add(policy)
            self.session.flush()
        return policy

    def update_provider(self, tenant_id: str, provider_key: str, provider_scope: str,
                        changes: Mapping[str, Any]) -> TenantProviderPolicyModel:
        unknown = set(changes) - {"processing_enabled", "active_jobs_limit", "single_enabled", "batch_enabled", "emergency_stop", "single_active_jobs_limit", "batch_active_jobs_limit", "daily_budget_limit_micros", "monthly_budget_limit_micros", "budget_currency", "allowed_models_json"}
        if unknown:
            raise ValueError(f"unsupported provider policy fields: {sorted(unknown)}")
        policy = self.get_or_create_provider(tenant_id, provider_key, provider_scope)
        for key, value in changes.items():
            setattr(policy, key, value)
        policy.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return policy

    def pause_provider(self, tenant_id: str, provider_key: str, provider_scope: str, *,
                       actor_id: str, reason: str) -> TenantProviderPolicyModel:
        if not reason.strip():
            raise ValueError("pause reason is required")
        policy = self.get_or_create_provider(tenant_id, provider_key, provider_scope)
        policy.processing_paused = True
        policy.pause_reason = reason.strip()
        policy.paused_by = actor_id
        policy.paused_at = datetime.now(timezone.utc)
        policy.updated_at = policy.paused_at
        self.session.flush()
        return policy

    def resume_provider(self, tenant_id: str, provider_key: str, provider_scope: str) -> TenantProviderPolicyModel:
        policy = self.get_or_create_provider(tenant_id, provider_key, provider_scope)
        policy.processing_paused = False
        policy.pause_reason = None
        policy.paused_by = None
        policy.paused_at = None
        policy.updated_at = datetime.now(timezone.utc)
        self.session.flush()
        return policy

    def audit(self, *, actor_id: str, tenant_id: str, action: str,
              old_policy: Mapping[str, Any], new_policy: Mapping[str, Any],
              reason: str | None = None, provider_key: str | None = None,
              provider_scope: str | None = None) -> ProcessingPolicyAuditModel:
        audit = ProcessingPolicyAuditModel(
            actor_id=actor_id, tenant_id=tenant_id, action=action,
            reason=reason, provider_key=provider_key, provider_scope=provider_scope,
            old_policy_json=dict(old_policy), new_policy_json=dict(new_policy),
        )
        self.session.add(audit)
        self.session.flush()
        return audit

    def list_providers(self, tenant_id: str) -> list[TenantProviderPolicyModel]:
        return list(self.session.scalars(
            select(TenantProviderPolicyModel).where(
                TenantProviderPolicyModel.tenant_id == tenant_id
            ).order_by(TenantProviderPolicyModel.provider_scope, TenantProviderPolicyModel.provider_key)
        ))

    def operational_job_counts(self, tenant_id: str) -> dict[str, int]:
        counts = self.job_counts(tenant_id)
        return {
            "queued": counts.get("pending", 0) + counts.get("retry", 0),
            "running": counts.get("processing", 0),
            "failed": counts.get("failed", 0),
            "completed": counts.get("completed", 0),
        }

    def job_counts(self, tenant_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(ProcessingJobModel.status, func.count()).where(
                ProcessingJobModel.tenant_id == tenant_id
            ).group_by(ProcessingJobModel.status)
        )
        return {status: int(count) for status, count in rows}
