from __future__ import annotations

from collections.abc import Callable
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.providers.registry import AiProviderRegistry
from app.providers.ai.gemini import GeminiAiMetadataProvider
from app.providers.ai.openai import OpenAiMetadataProvider
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository

_PACIFIC_TIME = ZoneInfo("America/Los_Angeles")


class _DatabaseGeminiQuotaCoordinator:
    def __init__(
        self, session_factory: Callable[[], Session], quota_scope: str, project_rpd: int
    ):
        self.session_factory = session_factory
        self.quota_scope = quota_scope
        self.project_rpd = project_rpd
        self.logger = logging.getLogger("cam.gemini_quota")

    def reserve_request(self, *, model: str, rpd: int, now: datetime):
        with self.session_factory() as session:
            decision = GeminiProjectQuotaRepository(session).reserve_request(
                quota_scope=self.quota_scope, model=model, rpd=rpd,
                project_rpd=self.project_rpd, now=now
            )
            session.commit()
        if decision.allowed:
            return None
        if decision.reason == "project_rpd_exhausted":
            self.logger.warning(
                "gemini_project_daily_cap_deferred",
                extra={
                    "quota_scope": self.quota_scope,
                    "quota_day": now.astimezone(_PACIFIC_TIME).date().isoformat(),
                    "project_reserved_requests": decision.reserved_requests,
                    "project_daily_limit": self.project_rpd,
                    "retry_at": (decision.available_at or now).isoformat(),
                    "model": model,
                    "provider_call_started": False,
                },
            )
        from app.providers.ai.gemini import GeminiModelUnavailable
        return GeminiModelUnavailable(
            model=model,
            reason=decision.reason or "rpd_exhausted",
            available_at=decision.available_at or now,
        )

    def block_until(self, *, model: str, retry_at: datetime) -> None:
        with self.session_factory() as session:
            GeminiProjectQuotaRepository(session).block_until(
                quota_scope=self.quota_scope, model=model, retry_at=retry_at
            )
            session.commit()


def build_ai_provider_registry(
    settings: Settings, *, session_factory: Callable[[], Session] | None = None
) -> AiProviderRegistry:
    """Build configured adapters without exposing provider SDKs to services."""

    registry = AiProviderRegistry()
    if settings.GEMINI_API_KEY:
        quota_coordinator = (
            _DatabaseGeminiQuotaCoordinator(
                session_factory, settings.GEMINI_PROJECT_QUOTA_SCOPE,
                settings.gemini_project_daily_request_limit,
            )
            if session_factory is not None
            else None
        )
        gemini = GeminiAiMetadataProvider(
            settings.GEMINI_API_KEY,
            model=settings.GEMINI_MODEL,
            timeout_seconds=settings.GEMINI_TIMEOUT_SECONDS,
            model_pool=settings.gemini_model_pool,
            model_limits=settings.gemini_model_limits,
            cooldown_seconds=settings.GEMINI_MODEL_COOLDOWN_SECONDS,
            quota_coordinator=quota_coordinator,
        )
        registry.register(gemini.provider_name, gemini)
    if settings.OPENAI_AI_ENABLED and settings.OPENAI_API_KEY:
        openai = OpenAiMetadataProvider(
            settings.OPENAI_API_KEY,
            model=settings.OPENAI_DEFAULT_MODEL,
            allowed_models=settings.openai_allowed_models,
            base_url=settings.OPENAI_BASE_URL,
            timeout_seconds=settings.OPENAI_TIMEOUT_SECONDS,
            max_retries=settings.OPENAI_MAX_RETRIES,
            image_detail=settings.OPENAI_IMAGE_DETAIL,
            store_responses=settings.OPENAI_STORE_RESPONSES,
            organization=settings.OPENAI_ORGANIZATION,
            project=settings.OPENAI_PROJECT,
            capture_raw_response=settings.AI_STORE_RAW_RESPONSE_ENABLED,
            max_image_bytes=settings.AI_ANALYSIS_MAX_OUTPUT_BYTES,
            batch_enabled=settings.OPENAI_BATCH_ENABLED,
            batch_completion_window=settings.OPENAI_BATCH_COMPLETION_WINDOW,
            batch_max_items=settings.OPENAI_BATCH_MAX_ITEMS,
            batch_max_file_bytes=settings.OPENAI_BATCH_MAX_FILE_BYTES,
            batch_poll_interval_seconds=settings.OPENAI_BATCH_POLL_INTERVAL_SECONDS,
            batch_result_chunk_size=settings.OPENAI_BATCH_RESULT_PAGE_OR_CHUNK_SIZE,
            batch_input_retention_hours=settings.OPENAI_BATCH_INPUT_RETENTION_HOURS,
            batch_output_retention_hours=settings.OPENAI_BATCH_OUTPUT_RETENTION_HOURS,
        )
        registry.register(openai.provider_name, openai)
    return registry
