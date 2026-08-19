from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import DeferredJobOutcome, JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import AiProviderError
from app.modules.ai_governance.gemini_quota import GeminiProjectQuotaRepository
from app.modules.ai_operations.credentials import (
    CreativeCredentialError,
    CreativeGeminiCredentialResolver,
)
from app.modules.assets.model import SourceAssetModel
from app.modules.pipeline.mime_types import is_eligible_video_source_asset
from app.modules.video_search.analysis import GeminiVideoAnalysisService
from app.modules.video_search.fingerprint import build_video_source_fingerprint
from app.modules.video_search.proxy import (
    VideoProxyConfigurationError,
    VideoProxyPreparationService,
    VideoProxyProcessError,
    VideoProxySourceChangedError,
    VideoProxyStorageError,
)
from app.modules.video_search.repository import (
    VideoChunkLayoutConflictError,
    VideoRunConflictError,
    VideoSearchRepository,
)
from app.modules.video_search.index_enqueue import enqueue_video_search_index_job
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_search.scheduler import (
    VideoFreeTierModelPlanner,
    VideoModelSelection,
    VideoNoSafeModel,
    VideoQuotaDeferral,
)
from app.providers.ai.gemini_video import GeminiVideoClient


class VideoAnalyzeJobHandler:
    """Runs one pinned, free-tier Gemini analysis attempt for a video source."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def __call__(self, context: JobHandlerContext) -> JobHandlerResult | DeferredJobOutcome:
        settings = self.settings or get_settings()
        if not all((
            settings.PROCESSING_JOBS_ENABLED, settings.VIDEO_SEARCH_ENABLED,
            settings.VIDEO_ANALYSIS_ENABLED, settings.VIDEO_PROXY_ENABLED,
        )):
            return JobHandlerResult.non_retryable("video_analysis_disabled", "Video analysis is disabled.")
        if settings.AI_EMERGENCY_STOP_ENABLED or settings.GEMINI_EMERGENCY_STOP_ENABLED:
            return DeferredJobOutcome(
                "video_gemini_emergency_stop",
                "Gemini video analysis is temporarily paused by emergency control.",
                datetime.now(timezone.utc) + timedelta(seconds=settings.GEMINI_MODEL_COOLDOWN_SECONDS),
            )
        source_id = context.job.payload.get("source_asset_id")
        if not isinstance(source_id, str) or not source_id or len(source_id) > 255:
            return JobHandlerResult.non_retryable("invalid_video_analysis_job", "video_analyze job requires a source_asset_id.")
        with context.dependencies.session_factory() as session:
            source = session.scalar(select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == context.job.tenant_id,
                SourceAssetModel.id == source_id,
                SourceAssetModel.deleted_at.is_(None),
            ))
            if source is None or not is_eligible_video_source_asset(source):
                return JobHandlerResult.non_retryable("video_source_unavailable", "Video source is unavailable or unsupported.")
            repo = VideoSearchRepository(session)
            profile = repo.get_active_profile(context.job.tenant_id)
            if profile is None:
                return JobHandlerResult.non_retryable("video_metadata_profile_unavailable", "No active video metadata profile is configured.")
            identity = {
                "tenant_id": context.job.tenant_id, "source_asset_id": source_id,
                "source_fingerprint": build_video_source_fingerprint(source),
                "video_metadata_profile_id": profile.id, "metadata_profile": profile.profile_name,
                "metadata_profile_version": profile.profile_version,
                "prompt_version": settings.VIDEO_AI_PROMPT_VERSION,
                "analysis_version": settings.VIDEO_AI_ANALYSIS_VERSION,
                "ai_provider": "gemini", "prompt_template": profile.prompt_template,
            }
            if repo.find_completed_compatible_run(**identity) is not None:
                return JobHandlerResult.completed()
            duration_ms = self._duration_ms(source, settings)
            try:
                resumable = repo.find_resumable_compatible_run(**identity)
            except VideoRunConflictError:
                return JobHandlerResult.non_retryable("video_resumable_run_conflict", "Multiple compatible resumable video runs exist.")
            resumable_id = resumable.id if resumable is not None else None
            pinned_model = resumable.ai_model if resumable is not None else None

        if settings.VIDEO_AI_REQUIRE_EXPLICIT_MODEL_LIMITS and not settings.GEMINI_MODEL_LIMITS.strip():
            return JobHandlerResult.non_retryable("video_gemini_limits_not_explicitly_configured", "Video Gemini limits must be explicitly configured.")
        if self._interrupted(context):
            return self._cancel(context, resumable_id)
        try:
            credential = CreativeGeminiCredentialResolver(context.dependencies.session_factory, settings).resolve(context.job.tenant_id)
        except CreativeCredentialError:
            return JobHandlerResult.non_retryable("video_gemini_credential_unavailable", "Video Gemini credential is unavailable.")

        quota_scope = f"{settings.GEMINI_PROJECT_QUOTA_SCOPE}:{credential.fingerprint[:12]}"
        with context.dependencies.session_factory() as session:
            planner = VideoFreeTierModelPlanner(settings, GeminiProjectQuotaRepository(session), quota_scope=quota_scope)
            decision = planner.select_pinned(model=pinned_model, duration_ms=duration_ms) if pinned_model else planner.select(duration_ms=duration_ms)
        if isinstance(decision, VideoQuotaDeferral):
            return DeferredJobOutcome("video_gemini_quota_deferred", "Gemini video capacity is temporarily unavailable.", decision.retry_at)
        if isinstance(decision, VideoNoSafeModel):
            code = "video_pinned_model_unconfigured" if pinned_model and any("not_explicitly_configured" in reason for reason in decision.reasons) else "video_no_model_fits_safe_tpm"
            return JobHandlerResult.non_retryable(code, "No configured Gemini model safely fits the video chunk.")

        operation = self._execute(context, settings, identity, decision, credential.secret, resumable_id)
        executor = context.dependencies.resources.get("async_executor")
        return executor.run(operation) if executor is not None else asyncio.run(operation)

    @staticmethod
    def _duration_ms(source: SourceAssetModel, settings: Settings) -> int:
        metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
        raw = (metadata.get("videoMediaMetadata") or {}).get("durationMillis") if isinstance(metadata.get("videoMediaMetadata"), dict) else None
        duration = settings.VIDEO_CHUNK_SECONDS * 1000
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            duration = min(raw, duration)
        return duration

    @staticmethod
    def _interrupted(context: JobHandlerContext) -> bool:
        return context.is_cancelled or context.shutdown_requested.is_set()

    def _cancel(self, context: JobHandlerContext, run_id: str | None) -> JobHandlerResult:
        if run_id:
            try:
                with context.dependencies.session_factory() as session:
                    repo = VideoSearchRepository(session)
                    run = repo.get_run(tenant_id=context.job.tenant_id, run_id=run_id)
                    if run is not None and run.status in {"pending", "preparing", "analyzing", "failed"}:
                        repo.cancel_run(tenant_id=context.job.tenant_id, run_id=run_id)
                        session.commit()
            except Exception:
                pass
        return JobHandlerResult.cancelled()

    async def _execute(self, context: JobHandlerContext, settings: Settings, identity: dict[str, Any], selection: VideoModelSelection, api_key: str, resumable_id: str | None) -> JobHandlerResult | DeferredJobOutcome:
        run_id: str | None = resumable_id
        proxy = VideoProxyPreparationService(context.dependencies.session_factory, settings)
        chunks = ()
        current_chunk_id: str | None = None
        try:
            if self._interrupted(context):
                return self._cancel(context, run_id)
            with context.dependencies.session_factory() as session:
                repo = VideoSearchRepository(session)
                if run_id:
                    run = repo.get_run(tenant_id=context.job.tenant_id, run_id=run_id)
                    if run is None or run.ai_model != selection.model:
                        return JobHandlerResult.non_retryable("video_pinned_model_conflict", "The resumable video run model is not available.")
                else:
                    values = {key: value for key, value in identity.items() if key != "prompt_template"}
                    run = repo.get_or_create_run(**values, ai_model=selection.model, chunk_seconds=settings.VIDEO_CHUNK_SECONDS)
                    run_id = run.id
                if run.status in {"pending", "failed"}:
                    repo.mark_run_preparing(tenant_id=context.job.tenant_id, run_id=run.id)
                session.commit()

            chunks = await proxy.prepare(tenant_id=context.job.tenant_id, source_asset_id=identity["source_asset_id"], expected_source_fingerprint=identity["source_fingerprint"])
            if self._interrupted(context):
                return self._cancel(context, run_id)

            with context.dependencies.session_factory() as session:
                repo = VideoSearchRepository(session)
                repo.create_chunks(tenant_id=context.job.tenant_id, run_id=run_id, layouts=(
                    {"chunk_index": item.chunk_index, "source_start_ms": item.source_start_ms, "source_end_ms": item.source_end_ms} for item in chunks
                ))
                run = repo.get_run(tenant_id=context.job.tenant_id, run_id=run_id)
                if run and run.status == "preparing":
                    repo.mark_run_analyzing(tenant_id=context.job.tenant_id, run_id=run_id)
                session.commit()
                stored = repo.list_chunks(tenant_id=context.job.tenant_id, run_id=run_id)

            prepared = {item.chunk_index: item for item in chunks}
            for row in stored:
                if row.status == "completed":
                    continue
                if self._interrupted(context):
                    return self._cancel(context, run_id)
                chunk = prepared.get(row.chunk_index)
                if chunk is None or (chunk.source_start_ms, chunk.source_end_ms) != (row.source_start_ms, row.source_end_ms):
                    return JobHandlerResult.non_retryable("video_chunk_layout_conflict", "Prepared video chunk layout differs from persisted layout.")

                current_chunk_id = row.id
                with context.dependencies.session_factory() as session:
                    planner = VideoFreeTierModelPlanner(settings, GeminiProjectQuotaRepository(session), quota_scope=selection.quota_scope)
                    reserved = planner.reserve(selection)
                    if not reserved.allowed:
                        session.rollback()
                        return DeferredJobOutcome("video_gemini_quota_deferred", "Gemini video capacity is temporarily unavailable.", reserved.available_at or datetime.now(timezone.utc))
                    repo = VideoSearchRepository(session)
                    repo.mark_chunk_preparing(tenant_id=context.job.tenant_id, run_id=run_id, chunk_id=row.id)
                    repo.mark_chunk_analyzing(tenant_id=context.job.tenant_id, run_id=run_id, chunk_id=row.id)
                    session.commit()

                if self._interrupted(context):
                    return self._cancel(context, run_id)
                client = GeminiVideoClient(api_key, model=selection.model, timeout_seconds=settings.GEMINI_TIMEOUT_SECONDS)
                result = await GeminiVideoAnalysisService(client, max_safe_input_tokens=selection.safe_tpm).analyze_chunk(chunk=chunk, prompt_template=identity["prompt_template"])
                with context.dependencies.session_factory() as session:
                    repo = VideoSearchRepository(session)
                    repo.complete_chunk(tenant_id=context.job.tenant_id, run_id=run_id, chunk_id=row.id, metadata_json=result.metadata_json, usage_json=result.usage_json, provider_metadata_json=result.provider_metadata_json)
                    session.commit()
                current_chunk_id = None

            with context.dependencies.session_factory() as session:
                repo = VideoSearchRepository(session)
                run = repo.get_run(tenant_id=context.job.tenant_id, run_id=run_id)
                completed = repo.complete_run(tenant_id=context.job.tenant_id, run_id=run_id, summary_json={"completed_chunks": run.completed_chunks, "total_chunks": run.total_chunks})
                enqueue_video_search_index_job(tenant_id=context.job.tenant_id, run=completed, processing=ProcessingRepository(session))
                session.commit()
            return JobHandlerResult.completed()
        except asyncio.CancelledError:
            return self._cancel(context, run_id)
        except VideoProxySourceChangedError:
            return self._non_retry(context, run_id, None, "video_source_changed", "Video source changed during proxy preparation.")
        except VideoProxyConfigurationError:
            return self._non_retry(context, run_id, current_chunk_id, "video_proxy_configuration_error", "Video proxy preparation is not configured.")
        except (VideoProxyStorageError, VideoProxyProcessError):
            return self._retry(context, run_id, current_chunk_id, "video_proxy_preparation_failed", "Video proxy preparation could not be completed.")
        except VideoChunkLayoutConflictError:
            return self._non_retry(context, run_id, None, "video_chunk_layout_conflict", "Prepared video chunk layout differs from persisted layout.")
        except AiProviderError as exc:
            if exc.status_code == 429:
                retry_at = self._rate_limit_retry_at(settings, exc)
                if current_chunk_id:
                    self._defer_chunk(context, run_id, current_chunk_id, exc.code, "Gemini video capacity is temporarily unavailable.")
                with context.dependencies.session_factory() as session:
                    GeminiProjectQuotaRepository(session).block_until(quota_scope=selection.quota_scope, model=selection.model, retry_at=retry_at)
                    session.commit()
                return DeferredJobOutcome("video_gemini_rate_limited", "Gemini video capacity is temporarily unavailable.", retry_at)
            if exc.retryable:
                return self._retry(context, run_id, current_chunk_id, exc.code, "Gemini video request could not be completed.")
            return self._non_retry(context, run_id, current_chunk_id, exc.code, "Gemini video returned an invalid response.")
        except Exception:
            return self._retry(context, run_id, current_chunk_id, "video_analysis_failed", "Video analysis could not be completed.")
        finally:
            proxy.cleanup(chunks)

    @staticmethod
    def _rate_limit_retry_at(settings: Settings, exc: AiProviderError) -> datetime:
        raw = exc.details.get("retry_after_seconds") if isinstance(exc.details, dict) else None
        seconds = raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= 3600 else settings.GEMINI_MODEL_COOLDOWN_SECONDS
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)

    @staticmethod
    def _defer_chunk(context: JobHandlerContext, run_id: str | None, chunk_id: str, code: str, message: str) -> None:
        if not run_id:
            return
        with context.dependencies.session_factory() as session:
            VideoSearchRepository(session).defer_chunk(tenant_id=context.job.tenant_id, run_id=run_id, chunk_id=chunk_id, error_code=code, error_message=message)
            session.commit()

    @staticmethod
    def _retry(context: JobHandlerContext, run_id: str | None, chunk_id: str | None, code: str, message: str) -> JobHandlerResult:
        if run_id:
            try:
                with context.dependencies.session_factory() as session:
                    repo = VideoSearchRepository(session)
                    if chunk_id:
                        repo.fail_chunk(tenant_id=context.job.tenant_id, run_id=run_id, chunk_id=chunk_id, error_code=code, error_message=message)
                    repo.fail_run(tenant_id=context.job.tenant_id, run_id=run_id, error_code=code, error_message=message)
                    session.commit()
            except Exception:
                pass
        return JobHandlerResult.retryable(code, message)

    @staticmethod
    def _non_retry(context: JobHandlerContext, run_id: str | None, chunk_id: str | None, code: str, message: str) -> JobHandlerResult:
        if run_id:
            try:
                with context.dependencies.session_factory() as session:
                    repo = VideoSearchRepository(session)
                    if chunk_id:
                        repo.fail_chunk(tenant_id=context.job.tenant_id, run_id=run_id, chunk_id=chunk_id, error_code=code, error_message=message)
                    repo.fail_run(tenant_id=context.job.tenant_id, run_id=run_id, error_code=code, error_message=message)
                    session.commit()
            except Exception:
                pass
        return JobHandlerResult.non_retryable(code, message)
