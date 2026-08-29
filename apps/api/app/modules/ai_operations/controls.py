from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_batch.model import AiBatchJobModel, BATCH_TERMINAL_STATUSES
from app.modules.ai_governance.repository import AiGovernanceRepository, MissingCostRateError
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_metadata.selection import AiProviderSelectionService
from app.modules.ai_operations.schema import AI_JOB_TYPES

RETRYABLE_AI_JOB_TYPES = AI_JOB_TYPES + ("video_analyze", "video_search_index")
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.repository import ProcessingPolicyRepository, policy_document
from app.modules.processing_policy.service import ProcessingPolicyService, TenantPolicyCache
from app.modules.video_search.model import VideoMetadataProfileModel


class AiOperationsControlError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


_DEFERRED_AI_REASON_CODES = frozenset({"gemini_quota_deferred"})

_DEFAULT_METADATA_PROMPT_TEMPLATE = 'Analyze the image and return JSON only. Extract search-ready visual metadata: a concise title, detailed description, primary subjects, objects, people (without identifying private individuals), actions, setting, scene type, style, colors, mood, composition, visible text, logos or brands, products, materials, patterns, seasons, events, concepts, keywords, and any useful search phrases. Be factual, include only information supported by the image, and use arrays where multiple values apply. {{ asset }}'
_DEFAULT_VIDEO_PROMPT_TEMPLATE = 'Analyze the video and return structured JSON describing useful semantic scenes and events, visible subjects, objects, actions, settings, composition, colors, mood, products, materials, logos, exact visible text, and audible speech. Use precise timestamps and include only evidence present in the video.'


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

    def set_video_pause(
        self, tenant_id: str, *, paused: bool, actor_id: str, reason: str,
    ) -> dict:
        # Video uses a separate provider scope, so this never pauses Image AI.
        self._provider("gemini")
        value = self.policy_service.set_provider_pause(
            tenant_id, "gemini", "video", paused=paused,
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

    def configuration(self, tenant_id: str, *, platform_admin: bool) -> dict[str, Any]:
        tenant = self.policies.get_or_create_tenant(tenant_id)
        capabilities = AiProviderSelectionService(
            self.settings, self.registry, self.policies
        ).capabilities(tenant_id)["providers"]
        providers = []
        for capability in capabilities:
            provider = capability["id"]
            policy = self.policies.get_provider(tenant_id, provider, "ai")
            last_error = self.session.scalar(
                select(AssetAiAnalysisModel.last_error_code).where(
                    AssetAiAnalysisModel.tenant_id == tenant_id,
                    AssetAiAnalysisModel.ai_provider == provider,
                    AssetAiAnalysisModel.last_error_code.is_not(None),
                ).order_by(AssetAiAnalysisModel.updated_at.desc()).limit(1)
            )
            providers.append({
                **capability,
                "connection_configured": self.registry.has(provider),
                "processing_enabled": policy.processing_enabled if policy else True,
                "paused": policy.processing_paused if policy else False,
                "single_enabled": policy.single_enabled if policy else "single" in capability["supported_modes"],
                "batch_enabled": policy.batch_enabled if policy else "batch" in capability["supported_modes"],
                "active_jobs_limit": policy.active_jobs_limit if policy else 1,
                "single_concurrency": policy.single_active_jobs_limit if policy else 1,
                "batch_concurrency": policy.batch_active_jobs_limit if policy else 1,
                "allowed_models": [item["id"] for item in capability["models"]],
                "last_error": last_error,
            })
        budget = self._budget_document(self.governance.get_policy(tenant_id)) or {
            "enabled": False, "daily_limit_micros": None,
            "monthly_limit_micros": None, "warning_threshold_percent": 80,
            "hard_stop_threshold_percent": 100, "currency": "USD",
        }
        profiles = list(self.session.scalars(select(MetadataProfileModel.profile_name).where(
            MetadataProfileModel.tenant_id == tenant_id,
            MetadataProfileModel.active.is_(True),
        ).distinct().order_by(MetadataProfileModel.profile_name)))
        prompt_statement = select(MetadataProfileModel).where(
            MetadataProfileModel.tenant_id == tenant_id,
            MetadataProfileModel.active.is_(True),
        )
        if tenant.default_metadata_profile:
            prompt_statement = prompt_statement.where(
                MetadataProfileModel.profile_name == tenant.default_metadata_profile,
            )
        prompt_profile = self.session.scalar(
            prompt_statement.order_by(MetadataProfileModel.created_at.desc()).limit(1)
        )
        video_prompt_profile = self.session.scalar(
            select(VideoMetadataProfileModel).where(
                VideoMetadataProfileModel.tenant_id == tenant_id,
                VideoMetadataProfileModel.active.is_(True),
            ).order_by(VideoMetadataProfileModel.created_at.desc()).limit(1)
        )
        video_policy = self.policies.get_provider(tenant_id, "gemini", "video")
        runtime_stopped, _ = self.governance.runtime_stopped("global")
        return {
            "tenant_id": tenant_id,
            "scope": {"tenant": tenant_id, "global_upper_bounds_read_only": True},
            "permissions": {
                "can_manage_tenant": True,
                "can_manage_global": platform_admin,
                "platform_admin": platform_admin,
            },
            "tenant": {
                "ai_enabled": tenant.ai_analysis_enabled,
                "video_enabled": (
                    video_policy is None
                    or (video_policy.processing_enabled and not video_policy.processing_paused)
                ),
                "processing_paused": tenant.processing_paused,
                "default_provider": tenant.default_ai_provider,
                "default_model": tenant.default_ai_model,
                "default_mode": tenant.default_ai_mode,
                "default_metadata_profile": tenant.default_metadata_profile,
                "auto_analyze_new_assets": tenant.auto_analyze_new_assets,
                "daily_item_limit": tenant.daily_ai_item_limit,
                "total_ai_concurrency": tenant.ai_active_jobs_limit,
                "retry_count": tenant.ai_retry_count,
                "timeout_seconds": tenant.ai_timeout_seconds,
            },
            "global": {
                "ai_auto_analyze_enabled": self.settings.AI_AUTO_ANALYZE_ENABLED,
                "single_enabled": self.settings.AI_SINGLE_ANALYSIS_ENABLED,
                "batch_enabled": self.settings.AI_BATCH_ANALYSIS_ENABLED,
                "emergency_stop": self.settings.AI_EMERGENCY_STOP_ENABLED or runtime_stopped,
            },
            "providers": providers,
            "metadata_profiles": profiles,
            "metadata_prompt_template": self._prompt_template_document(prompt_profile),
            "video_prompt_template": self._video_prompt_template_document(video_prompt_profile),
            "budget": budget,
        }

    def update_metadata_prompt_template(
        self, tenant_id: str, *, prompt_template: str, actor_id: str, reason: str,
    ) -> dict[str, Any]:
        tenant = self.policies.get_or_create_tenant(tenant_id)
        statement = select(MetadataProfileModel).where(
            MetadataProfileModel.tenant_id == tenant_id,
            MetadataProfileModel.active.is_(True),
        )
        if tenant.default_metadata_profile:
            statement = statement.where(
                MetadataProfileModel.profile_name == tenant.default_metadata_profile,
            )
        current = self.session.scalar(
            statement.order_by(MetadataProfileModel.created_at.desc()).limit(1)
        )
        template = prompt_template.strip()
        if not template:
            raise AiOperationsControlError(
                "metadata_prompt_template_required", "Prompt template cannot be empty.",
            )
        if current is not None and template == current.prompt_template:
            return self._prompt_template_document(current)
        version_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        if current is not None:
            profile_name = current.profile_name
            next_version = f"{current.profile_version[:72]}-edit-{version_stamp}"
            current.active = False
            optional_json_schema = dict(current.optional_json_schema) if current.optional_json_schema else None
            search_config_json = dict(current.search_config_json or {})
        else:
            profile_name = tenant.default_metadata_profile or "creative-default"
            has_version_one = self.session.scalar(select(MetadataProfileModel.id).where(
                MetadataProfileModel.tenant_id == tenant_id,
                MetadataProfileModel.profile_name == profile_name,
                MetadataProfileModel.profile_version == "1",
            ).limit(1)) is not None
            next_version = f"created-{version_stamp}" if has_version_one else "1"
            optional_json_schema = None
            search_config_json = {}
            if tenant.default_metadata_profile is None:
                tenant.default_metadata_profile = profile_name
        replacement = MetadataProfileModel(
            tenant_id=tenant_id,
            profile_name=profile_name,
            profile_version=next_version,
            prompt_template=template,
            optional_json_schema=optional_json_schema,
            search_config_json=search_config_json,
            active=True,
        )
        self.session.add(replacement)
        self.session.flush()
        self.governance.event(
            tenant_id,
            "metadata_prompt_template_updated",
            actor_id=actor_id,
            reason=reason,
            details={
                "profile_name": replacement.profile_name,
                "previous_profile_id": current.id if current else None,
                "previous_profile_version": current.profile_version if current else None,
                "profile_id": replacement.id,
                "profile_version": replacement.profile_version,
            },
        )
        return self._prompt_template_document(replacement)

    def update_video_prompt_template(
        self, tenant_id: str, *, prompt_template: str, actor_id: str, reason: str,
    ) -> dict[str, Any]:
        current = self.session.scalar(
            select(VideoMetadataProfileModel).where(
                VideoMetadataProfileModel.tenant_id == tenant_id,
                VideoMetadataProfileModel.active.is_(True),
            ).order_by(VideoMetadataProfileModel.created_at.desc()).limit(1)
        )
        template = prompt_template.strip()
        if not template:
            raise AiOperationsControlError(
                "video_prompt_template_required", "Video prompt template cannot be empty.",
            )
        if current is not None and template == current.prompt_template:
            return self._video_prompt_template_document(current)
        version_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        if current is not None:
            profile_name = current.profile_name
            next_version = f"{current.profile_version[:72]}-edit-{version_stamp}"
            current.active = False
            optional_json_schema = dict(current.optional_json_schema) if current.optional_json_schema else None
            search_config_json = dict(current.search_config_json or {})
        else:
            profile_name = "video-default"
            has_version_one = self.session.scalar(select(VideoMetadataProfileModel.id).where(
                VideoMetadataProfileModel.tenant_id == tenant_id,
                VideoMetadataProfileModel.profile_name == profile_name,
                VideoMetadataProfileModel.profile_version == "1",
            ).limit(1)) is not None
            next_version = f"created-{version_stamp}" if has_version_one else "1"
            optional_json_schema = None
            search_config_json = {}
        replacement = VideoMetadataProfileModel(
            tenant_id=tenant_id,
            profile_name=profile_name,
            profile_version=next_version,
            prompt_template=template,
            optional_json_schema=optional_json_schema,
            search_config_json=search_config_json,
            active=True,
        )
        self.session.add(replacement)
        self.session.flush()
        self.governance.event(
            tenant_id,
            "video_prompt_template_updated",
            actor_id=actor_id,
            reason=reason,
            details={
                "profile_name": replacement.profile_name,
                "previous_profile_id": current.id if current else None,
                "previous_profile_version": current.profile_version if current else None,
                "profile_id": replacement.id,
                "profile_version": replacement.profile_version,
            },
        )
        return self._video_prompt_template_document(replacement)

    @staticmethod
    def _video_prompt_template_document(profile: VideoMetadataProfileModel | None) -> dict[str, Any]:
        if profile is None:
            return {
                "id": None, "profile_name": "video-default", "profile_version": "Draft",
                "prompt_template": _DEFAULT_VIDEO_PROMPT_TEMPLATE,
                "updated_at": None, "is_draft": True,
            }
        return {
            "id": profile.id,
            "profile_name": profile.profile_name,
            "profile_version": profile.profile_version,
            "prompt_template": profile.prompt_template,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            "is_draft": False,
        }

    @staticmethod
    def _prompt_template_document(profile: MetadataProfileModel | None) -> dict[str, Any]:
        if profile is None:
            return {
                "id": None,
                "profile_name": "creative-default",
                "profile_version": "Draft",
                "prompt_template": _DEFAULT_METADATA_PROMPT_TEMPLATE,
                "updated_at": None,
                "is_draft": True,
            }
        return {
            "id": profile.id,
            "profile_name": profile.profile_name,
            "profile_version": profile.profile_version,
            "prompt_template": profile.prompt_template,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            "is_draft": False,
        }

    def update_configuration(
        self, tenant_id: str, changes: Mapping[str, Any], *, actor_id: str, reason: str,
    ) -> dict:
        mapped = {
            "default_mode": "default_ai_mode",
            "default_metadata_profile": "default_metadata_profile",
            "auto_analyze_new_assets": "auto_analyze_new_assets",
            "daily_item_limit": "daily_ai_item_limit",
            "retry_count": "ai_retry_count",
            "timeout_seconds": "ai_timeout_seconds",
        }
        profile = changes.get("default_metadata_profile")
        if profile and self.session.scalar(select(MetadataProfileModel.id).where(
            MetadataProfileModel.tenant_id == tenant_id,
            MetadataProfileModel.profile_name == profile,
            MetadataProfileModel.active.is_(True),
        ).limit(1)) is None:
            raise AiOperationsControlError("metadata_profile_unavailable", "The metadata profile is not active for this tenant.")
        mode = changes.get("default_mode")
        tenant = self.policies.get_or_create_tenant(tenant_id)
        if mode and tenant.default_ai_provider:
            capabilities = AiProviderSelectionService(
                self.settings, self.registry, self.policies
            ).capabilities(tenant_id)["providers"]
            selected = next((item for item in capabilities if item["id"] == tenant.default_ai_provider), None)
            if selected is None or mode not in selected["supported_modes"]:
                raise AiOperationsControlError("ai_mode_unavailable", "The selected provider does not support this mode.")
        policy = self.policy_service.update(
            tenant_id, {mapped[key]: value for key, value in changes.items()},
            actor_id=actor_id, reason=reason,
        )
        return policy_document(policy)
    def retry_job(
        self, tenant_id: str, job_id: str, *, actor_id: str, reason: str,
        force: bool = False,
    ) -> tuple[dict, str]:
        job = self._locked_job(tenant_id, job_id)
        if job.job_type not in RETRYABLE_AI_JOB_TYPES:
            raise AiOperationsControlError("job_not_ai", "Only AI jobs can be retried.")
        if job.status == "retry" and job.last_error_code == "operator_retry_requested":
            return _job_document(job), "already_requested"
        now = datetime.now(timezone.utc)
        retry_at = job.next_attempt_at
        if retry_at is not None and retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        deferred = (
            job.status == "pending"
            and job.last_error_code in _DEFERRED_AI_REASON_CODES
            and retry_at is not None
            and retry_at > now
        )
        if deferred:
            if not force:
                raise AiOperationsControlError(
                    "job_deferred", "This job is waiting for Gemini quota. Use force retry to run it now.",
                    status_code=409,
                )
            before = _job_document(job)
            job.next_attempt_at = now
            job.claimed_by = None
            job.claimed_at = None
            job.lease_expires_at = None
            job.updated_at = now
            self.session.flush()
            after = _job_document(job)
            self._audit_job(tenant_id, actor_id, reason, "ai_job_force_retry_requested", before, after, job)
            return after, "force_retry_requested"
        if job.status != "failed" or job.last_error_code in {
            "operation_cancelled", "analysis_cancelled", "batch_cancelled",
        }:
            raise AiOperationsControlError(
                "job_not_retryable", "The job is not eligible for retry.", status_code=409,
            )
        before = _job_document(job)
        job.status = "retry"
        job.next_attempt_at = now
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

    def retry_jobs_by_error_code(
        self, tenant_id: str, error_code: str, *, actor_id: str, reason: str,
        limit: int, job_type: str | None = None,
    ) -> dict[str, Any]:
        """Retry a bounded group of terminal AI jobs sharing an error code."""
        eligible_job_types = RETRYABLE_AI_JOB_TYPES
        if job_type is not None:
            if job_type not in RETRYABLE_AI_JOB_TYPES:
                raise AiOperationsControlError(
                    "job_type_not_retryable", "The requested job type is not eligible for retry.", status_code=422,
                )
            eligible_job_types = (job_type,)
        job_ids = list(self.session.scalars(
            select(ProcessingJobModel.id).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.job_type.in_(eligible_job_types),
                ProcessingJobModel.status == "failed",
                ProcessingJobModel.last_error_code == error_code,
            ).order_by(ProcessingJobModel.updated_at, ProcessingJobModel.id).limit(limit)
        ))
        retried = 0
        skipped = 0
        items: list[dict[str, Any]] = []
        for job_id in job_ids:
            try:
                document, outcome = self.retry_job(
                    tenant_id, job_id, actor_id=actor_id, reason=reason,
                )
                retried += 1
                items.append({"job_id": job_id, "outcome": outcome, "job": document})
            except AiOperationsControlError as exc:
                skipped += 1
                items.append({"job_id": job_id, "outcome": "skipped", "code": exc.code})
        return {
            "error_code": error_code,
            "job_type": job_type,
            "matched": len(job_ids),
            "retried": retried,
            "skipped": skipped,
            "items": items,
        }

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
            "warning_threshold_percent": value.warning_threshold_percent,
            "hard_stop_threshold_percent": value.hard_stop_threshold_percent,
            "updated_at": value.updated_at.isoformat(),
        }
