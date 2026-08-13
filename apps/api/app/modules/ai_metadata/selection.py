from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings
from app.domain.providers.registry import AiProviderRegistry
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.modules.processing_policy.service import ProcessingPolicyService

AiProviderName = Literal["gemini", "openai"]
ProcessingMode = Literal["single", "batch"]

_PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "openai": "OpenAI",
}


class AiSelectionError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class AiSelection:
    provider: AiProviderName
    model: str
    processing_mode: ProcessingMode


class AiProviderSelectionService:
    """Resolve public AI choices against global, registry and tenant policy."""

    def __init__(
        self,
        settings: Settings,
        registry: AiProviderRegistry,
        policy_repository: ProcessingPolicyRepository,
    ):
        self.settings = settings
        self.registry = registry
        self.policy_repository = policy_repository

    def resolve(
        self,
        *,
        tenant_id: str,
        provider: AiProviderName,
        processing_mode: ProcessingMode,
        model: str | None,
    ) -> AiSelection:
        adapter = self._available_adapter(
            tenant_id=tenant_id,
            provider=provider,
            processing_mode=processing_mode,
        )
        allowed = self._allowed_models(provider)
        provider_policy = self.policy_repository.get_provider(tenant_id, provider, "ai")
        if provider_policy is not None and provider_policy.allowed_models_json is not None:
            allowed = tuple(model for model in allowed if model in provider_policy.allowed_models_json)
        selected_model = (model or self._default_model(provider)).strip()
        if not selected_model or selected_model not in allowed:
            raise AiSelectionError(
                "ai_model_not_allowed",
                "The requested AI model is not allowed.",
                status_code=422,
            )
        if adapter.default_model is None:
            raise AiSelectionError(
                "ai_provider_unavailable",
                "The requested AI provider has no configured default model.",
                status_code=503,
            )
        return AiSelection(provider, selected_model, processing_mode)

    def capabilities(self, tenant_id: str) -> dict:
        providers = []
        for provider in ("gemini", "openai"):
            modes = [
                mode
                for mode in ("single", "batch")
                if self._mode_available(tenant_id, provider, mode)
            ]
            allowed = self._allowed_models(provider)
            provider_policy = self.policy_repository.get_provider(tenant_id, provider, "ai")
            if provider_policy is not None and provider_policy.allowed_models_json is not None:
                allowed = tuple(model for model in allowed if model in provider_policy.allowed_models_json)
            providers.append({
                "id": provider,
                "label": _PROVIDER_LABELS[provider],
                "enabled": bool(modes),
                "models": [
                    {
                        "id": model,
                        "label": model,
                        "supports_single": "single" in modes,
                        "supports_batch": "batch" in modes,
                    }
                    for model in allowed
                ],
                "default_model": self._default_model(provider),
                "supported_modes": modes,
            })
        return {"providers": providers}

    def _available_adapter(
        self,
        *,
        tenant_id: str,
        provider: str,
        processing_mode: str,
    ):
        if not self._mode_available(tenant_id, provider, processing_mode):
            raise AiSelectionError(
                "ai_provider_unavailable",
                "The requested AI provider or processing mode is unavailable.",
                status_code=503,
            )
        adapter = self.registry.get(provider)
        if adapter is None:
            raise AiSelectionError(
                "ai_provider_unavailable",
                "The requested AI provider is unavailable.",
                status_code=503,
            )
        return adapter

    def _mode_available(
        self,
        tenant_id: str,
        provider: str,
        processing_mode: str,
    ) -> bool:
        if provider not in _PROVIDER_LABELS:
            return False
        if self.settings.AI_EMERGENCY_STOP_ENABLED or bool(getattr(self.settings, f"{provider.upper()}_EMERGENCY_STOP_ENABLED", False)):
            return False
        if not self.settings.DYNAMIC_AI_METADATA_ENABLED:
            return False
        if not self.settings.PROCESSING_JOBS_ENABLED:
            return False
        if processing_mode == "single":
            if not self.settings.AI_SINGLE_ANALYSIS_ENABLED:
                return False
        elif processing_mode == "batch":
            if not self.settings.AI_BATCH_ANALYSIS_ENABLED:
                return False
            if provider == "openai" and not self.settings.OPENAI_BATCH_ENABLED:
                return False
        else:
            return False
        # Gemini is backed by a runtime tenant credential resolver. It may be
        # configured in the database even when no deployment fallback exists.
        if provider == "openai" and not (
            self.settings.OPENAI_AI_ENABLED and self.settings.OPENAI_API_KEY
        ):
            return False
        adapter = self.registry.get(provider)
        if adapter is None:
            return False
        if processing_mode == "single" and not adapter.supports_single:
            return False
        if processing_mode == "batch" and not adapter.supports_batch:
            return False
        effective = ProcessingPolicyService(
            self.policy_repository, self.settings
        ).effective(tenant_id)
        if not effective.effective.get("ai_analysis_enabled", False):
            return False
        if AiGovernanceRepository(self.policy_repository.session).runtime_stopped(provider)[0]:
            return False
        provider_policy = self.policy_repository.get_provider(
            tenant_id, provider, "ai"
        )
        return provider_policy is None or (
            provider_policy.processing_enabled
            and not provider_policy.processing_paused
            and not provider_policy.emergency_stop
            and (processing_mode != "single" or provider_policy.single_enabled)
            and (processing_mode != "batch" or provider_policy.batch_enabled)
        )

    def _allowed_models(self, provider: str) -> tuple[str, ...]:
        if provider == "gemini":
            return self.settings.gemini_allowed_models
        if provider == "openai":
            return self.settings.openai_allowed_models
        return ()

    def _default_model(self, provider: str) -> str:
        if provider == "gemini":
            return self.settings.GEMINI_MODEL
        if provider == "openai":
            return self.settings.OPENAI_DEFAULT_MODEL
        return ""
