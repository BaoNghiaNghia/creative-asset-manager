from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.providers.ai.gemini import GeminiPoolTemporarilyUnavailable

from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataProvider,
    AiProviderError,
    AssetStorageProvider,
    OpenStoredAssetInput,
)
from app.modules.ai_metadata.analysis_image import (
    AnalysisImageError,
    AnalysisImageLimits,
    AnalysisImagePreparer,
)
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.result_importer import AiAnalysisResultImporter
from app.modules.ai_metadata.validator import MetadataDocumentValidator
from app.modules.ai_governance.metrics import AI_METRICS
from app.modules.ai_governance.repository import AiGovernanceRepository, MissingCostRateError, ProviderGovernanceBlocked
from app.modules.ai_governance.service import AiBudgetService, usage_units
from app.modules.assets.model import AssetModel
from app.modules.storage.repository import ManagedStorageRepository


@dataclass(frozen=True, slots=True)
class AiAnalysisOutcome:
    status: Literal[
        "completed",
        "retryable_failure",
        "non_retryable_failure",
        "cancelled",
        "budget_blocked",
        "deferred",
    ]
    error_code: str | None = None
    error_message: str | None = None
    retry_at: datetime | None = None
    metadata: Mapping[str, object] | None = None


class AiAnalysisService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        storage_provider: AssetStorageProvider,
        ai_provider: AiMetadataProvider,
        settings: Settings,
        projection_builder: SearchProjectionBuilder | None = None,
        validator: MetadataDocumentValidator | None = None,
    ):
        self.session_factory = session_factory
        self.storage_provider = storage_provider
        self.ai_provider = ai_provider
        self.settings = settings
        self.projection_builder = projection_builder or SearchProjectionBuilder()
        self.validator = validator or MetadataDocumentValidator()

    async def analyze(
        self,
        *,
        tenant_id: str,
        analysis_id: str,
        worker_id: str,
        is_cancelled: Callable[[], bool] | None = None,
        job_id: str | None = None,
        pilot_run_id: str | None = None,
        enqueue_index: bool = True,
    ) -> AiAnalysisOutcome:
        if not (
            self.settings.DYNAMIC_AI_METADATA_ENABLED
            and self.settings.AI_SINGLE_ANALYSIS_ENABLED
        ):
            return AiAnalysisOutcome(
                "non_retryable_failure",
                "ai_single_analysis_disabled",
                "Single-asset AI analysis is disabled.",
            )

        attempt_count = 0
        operation_key = None
        reservation_id = None
        provider_name = str(getattr(self.ai_provider, "provider_name", "gemini"))
        provider_model = str(getattr(self.ai_provider, "model", self.settings.GEMINI_MODEL))
        estimated_cost_micros = 0
        profile_name = profile_version = prompt_version = asset_id = None
        try:
            with self.session_factory() as session:
                repository = AiMetadataRepository(session, self.validator)
                analysis = repository.get_analysis(analysis_id)
                if analysis.tenant_id != tenant_id:
                    return AiAnalysisOutcome(
                        "non_retryable_failure", "analysis_not_found", "Analysis was not found."
                    )
                if analysis.status == "completed":
                    return AiAnalysisOutcome("completed")
                claimed = repository.claim_analysis(
                    analysis_id,
                    worker_id=worker_id,
                    lease_seconds=self.settings.AI_ANALYSIS_LEASE_SECONDS,
                )
                if claimed is None:
                    session.rollback()
                    return AiAnalysisOutcome("completed")
                session.refresh(claimed)
                asset = session.get(AssetModel, claimed.asset_id)
                profile = repository.get_profile(claimed.metadata_profile_id)
                storage = ManagedStorageRepository(session).get(
                    tenant_id, claimed.asset_id, "google_drive_managed"
                )
                if asset is None or asset.content_hash != claimed.content_hash:
                    repository.fail_analysis(
                        analysis_id,
                        error_code="asset_identity_changed",
                        error_message="Asset identity changed before analysis.",
                        retryable=False,
                    )
                    session.commit()
                    return AiAnalysisOutcome(
                        "non_retryable_failure",
                        "asset_identity_changed",
                        "Asset identity changed before analysis.",
                    )
                if not profile.active:
                    repository.fail_analysis(
                        analysis_id,
                        error_code="metadata_profile_inactive",
                        error_message="The selected metadata profile is not active.",
                        retryable=False,
                    )
                    session.commit()
                    return AiAnalysisOutcome(
                        "non_retryable_failure",
                        "metadata_profile_inactive",
                        "The selected metadata profile is not active.",
                    )
                if not (asset.mime_type or "").lower().startswith("image/"):
                    repository.fail_analysis(
                        analysis_id,
                        error_code="unsupported_asset_type",
                        error_message="Single-asset analysis currently supports images only.",
                        retryable=False,
                    )
                    session.commit()
                    return AiAnalysisOutcome(
                        "non_retryable_failure",
                        "unsupported_asset_type",
                        "Single-asset analysis currently supports images only.",
                    )

                if storage is None or storage.status != "stored" or not storage.remote_file_id:
                    repository.fail_analysis(
                        analysis_id,
                        error_code="managed_asset_not_stored",
                        error_message="A stored managed asset is required for analysis.",
                        retryable=True,
                        terminal=False,
                    )
                    session.commit()
                    return AiAnalysisOutcome(
                        "retryable_failure",
                        "managed_asset_not_stored",
                        "A stored managed asset is required for analysis.",
                    )
                open_input = OpenStoredAssetInput(
                    tenant_id=tenant_id,
                    asset_id=asset.id,
                    remote_file_id=storage.remote_file_id,
                    content_type=asset.mime_type,
                    size_bytes=asset.size_bytes,
                )
                prompt = profile.prompt_template.replace("{{ asset }}", asset.id)
                prompt_version = claimed.prompt_version
                asset_id = asset.id
                profile_name = profile.profile_name
                profile_version = profile.profile_version
                schema = profile.optional_json_schema
                search_config = profile.search_config_json
                attempt_count = claimed.attempt_count
                session.commit()

            if is_cancelled is not None and is_cancelled():
                return await self._cancel(analysis_id)

            preparer = AnalysisImagePreparer(
                self.storage_provider,
                limits=AnalysisImageLimits(
                    max_source_bytes=self.settings.AI_ANALYSIS_MAX_SOURCE_BYTES,
                    max_output_bytes=self.settings.AI_ANALYSIS_MAX_OUTPUT_BYTES,
                    max_width=self.settings.AI_ANALYSIS_MAX_WIDTH,
                    max_height=self.settings.AI_ANALYSIS_MAX_HEIGHT,
                    max_pixels=self.settings.AI_ANALYSIS_MAX_PIXELS,
                    jpeg_quality=self.settings.AI_ANALYSIS_JPEG_QUALITY,
                ),
            )
            prepared = await preparer.prepare(open_input)
            with self.session_factory() as session:
                repository = AiMetadataRepository(session, self.validator)
                current = repository.get_analysis(analysis_id)
                if current.status == "completed":
                    return AiAnalysisOutcome("completed")
                repository.save_analysis_image_hash(
                    tenant_id=tenant_id,
                    asset_id=current.asset_id,
                    image_hash=prepared.content_hash,
                )
                repository.set_stage(analysis_id, "analyzing")
                session.commit()

            operation_key = f"analysis:{analysis_id}:provider:{provider_name}:model:{provider_model}:mode:single:attempt:{attempt_count}"
            estimated_input_units = max(1, (len(prompt) + 3) // 4)
            with self.session_factory() as session:
                governance = AiGovernanceRepository(session)
                try:
                    governance.assert_provider_allowed(tenant_id, provider_name, "single")
                    rate = governance.require_cost_rate(provider_name, provider_model, "single")
                except MissingCostRateError as exc:
                    if governance.has_budget_override(tenant_id, analysis_id):
                        rate = None
                        governance.event(tenant_id, "missing_cost_rate_override_used", reason=str(exc), details={"provider": provider_name, "processing_mode": "single", "model": provider_model, "mode": "single"})
                    else:
                        AiMetadataRepository(session, self.validator).mark_budget_blocked(analysis_id, code=exc.code, reason=str(exc))
                        governance.event(tenant_id, exc.code, reason=str(exc), details={"provider": provider_name, "processing_mode": "single", "model": provider_model, "mode": "single"})
                        AI_METRICS.increment("budget_blocks", provider=provider_name, mode="single", outcome=exc.code)
                        session.commit()
                        return AiAnalysisOutcome("budget_blocked", exc.code, str(exc))
                except ProviderGovernanceBlocked as exc:
                    AiMetadataRepository(session, self.validator).mark_budget_blocked(analysis_id, code=exc.code, reason=exc.reason)
                    governance.event(tenant_id, exc.code, reason=exc.reason, details={"provider": provider_name, "processing_mode": "single", "model": provider_model, "mode": "single"})
                    session.commit()
                    return AiAnalysisOutcome("budget_blocked", exc.code, exc.reason)
                reservation_id = None
                if rate is not None:
                    estimated_cost_micros = governance.estimate_cost(rate, estimated_input_units, self.settings.AI_ESTIMATED_OUTPUT_UNITS, 1)
                    decision = AiBudgetService(governance, self.settings).reserve(tenant_id=tenant_id, operation_key=operation_key, estimated_cost_micros=estimated_cost_micros, analysis_id=analysis_id, job_id=job_id, pilot_run_id=pilot_run_id, currency=rate.currency, provider=provider_name, model=provider_model, processing_mode="single", attempt_number=attempt_count)
                    reservation_id = decision.reservation_id
                    if not decision.allowed:
                        AiMetadataRepository(session, self.validator).mark_budget_blocked(analysis_id, code=decision.code or "budget_blocked", reason=decision.reason or "AI budget blocked this operation.")
                        governance.record_usage(tenant_id=tenant_id, operation_key=operation_key, values={"asset_id": asset_id, "analysis_id": analysis_id, "job_id": job_id, "provider": provider_name, "processing_mode": "single", "model": provider_model, "metadata_profile": profile_name, "metadata_profile_version": profile_version, "prompt_version": prompt_version, "input_units": 0, "output_units": 0, "media_units": 0, "locally_estimated_cost_micros": estimated_cost_micros, "currency": rate.currency, "latency_ms": 0, "outcome": "budget_blocked", "retry_count": max(0, attempt_count - 1)})
                        session.commit()
                        return AiAnalysisOutcome("budget_blocked" if decision.action == "defer" else "non_retryable_failure", decision.code, decision.reason)
                session.commit()
            with self.session_factory() as session:
                governance = AiGovernanceRepository(session)
                try:
                    governance.assert_provider_allowed(tenant_id, provider_name, "single")
                except ProviderGovernanceBlocked as exc:
                    AiMetadataRepository(session, self.validator).mark_budget_blocked(analysis_id, code=exc.code, reason=exc.reason)
                    session.commit()
                    return AiAnalysisOutcome("budget_blocked", exc.code, exc.reason)
            provider_started = time.monotonic()
            result = await self.ai_provider.analyze_single(
                AiMetadataAnalysisInput(
                    tenant_id=tenant_id,
                    asset_id=open_input.asset_id,
                    prompt=prompt,
                    image_bytes=prepared.content,
                    image_mime_type=prepared.mime_type,
                    metadata_profile=profile_name,
                    metadata_profile_version=profile_version,
                    json_schema=schema,
                    is_cancelled=is_cancelled,
                )
            )
            provider_latency_ms = int((time.monotonic() - provider_started) * 1000)
            input_units, output_units, media_units = usage_units(result.usage)
            with self.session_factory() as session:
                governance = AiGovernanceRepository(session)
                rate = governance.resolve_cost_rate(provider_name, result.model or provider_model, processing_mode="single")
                local_actual = governance.estimate_cost(rate, input_units, output_units, media_units) if rate is not None else None
                reported = result.usage.get("costMicros")
                reported_micros = max(0, int(reported)) if isinstance(reported, (int, float)) else None
                actual_cost = reported_micros if reported_micros is not None else local_actual
                if reservation_id and actual_cost is not None:
                    AiBudgetService(governance, self.settings).reconcile(reservation_id, actual_cost)
                governance.record_usage(tenant_id=tenant_id, operation_key=operation_key, values={
                    "asset_id": asset_id, "analysis_id": analysis_id, "job_id": job_id,
                    "provider": result.provider or provider_name, "processing_mode": "single", "model": result.model or provider_model,
                    "metadata_profile": profile_name, "metadata_profile_version": profile_version,
                    "prompt_version": prompt_version, "input_units": input_units,
                    "output_units": output_units, "media_units": media_units,
                    "provider_reported_cost_micros": reported_micros,
                    "locally_estimated_cost_micros": local_actual,
                    "currency": rate.currency if rate is not None else "USD", "latency_ms": provider_latency_ms,
                    "outcome": "completed", "retry_count": max(0, attempt_count - 1),
                    "provider_request_id": result.provider_request_id,
                })
                session.commit()
            AI_METRICS.increment("ai_requests", provider=provider_name, outcome="completed")
            AI_METRICS.increment("input_units", provider=provider_name, outcome="completed", value=input_units)
            AI_METRICS.increment("output_units", provider=provider_name, outcome="completed", value=output_units)
            if local_actual is not None:
                AI_METRICS.increment("estimated_cost_micros", provider=provider_name, mode="single", outcome="completed", value=local_actual)
            if actual_cost is not None:
                AI_METRICS.increment("actual_cost_micros", provider=provider_name, mode="single", outcome="completed", value=actual_cost)
            AI_METRICS.increment("media_units", provider=provider_name, outcome="completed", value=media_units)
            AI_METRICS.increment("breaker_state", provider=provider_name, outcome="closed")
            AI_METRICS.latency(provider_name, "completed", provider_latency_ms)
            with self.session_factory() as session:
                repository = AiMetadataRepository(session, self.validator)
                if not repository.owns_active_lease(
                    analysis_id, worker_id=worker_id
                ):
                    session.rollback()
                    return AiAnalysisOutcome(
                        "cancelled", "analysis_lease_lost", "Analysis lease was lost."
                    )
                repository.set_stage(analysis_id, "validating")
                session.commit()

            with self.session_factory() as session:
                imported = AiAnalysisResultImporter(
                    session, self.settings, validator=self.validator,
                    projection_builder=self.projection_builder,
                ).import_result(
                    tenant_id=tenant_id, analysis_id=analysis_id, result=result,
                    enqueue_index=enqueue_index,
                )
                analysis = AiMetadataRepository(session, self.validator).get_analysis(analysis_id)
                if imported.status == "invalid_metadata" and operation_key:
                    AiGovernanceRepository(session).record_usage(
                        tenant_id=tenant_id, operation_key=operation_key,
                        values={"outcome": "invalid_metadata"},
                    )
                retryable = bool(analysis.failure_retryable)
                session.commit()
            if imported.status == "invalid_metadata":
                AI_METRICS.increment(
                    "invalid_metadata", provider=provider_name,
                    outcome="validation_failed",
                )
                return AiAnalysisOutcome(
                    "retryable_failure" if retryable else "non_retryable_failure",
                    "metadata_validation_failed",
                    "AI metadata failed safety or profile validation.",
                )
            return AiAnalysisOutcome("completed")
        except AnalysisImageError as exc:
            await self._record_failure(
                analysis_id, code=exc.code, message=str(exc), retryable=exc.retryable
            )
            return AiAnalysisOutcome(
                "retryable_failure" if exc.retryable else "non_retryable_failure",
                exc.code,
                str(exc),
            )
        except GeminiPoolTemporarilyUnavailable as exc:
            if reservation_id:
                with self.session_factory() as session:
                    AiBudgetService(
                        AiGovernanceRepository(session), self.settings
                    ).reconcile(reservation_id, 0)
                    session.commit()
            await self._record_failure(
                analysis_id,
                code="gemini_quota_deferred",
                message="Gemini model capacity is temporarily unavailable.",
                retryable=True,
                provider_metadata=exc.details,
            )
            return AiAnalysisOutcome(
                "deferred",
                "gemini_quota_deferred",
                "Gemini model capacity is temporarily unavailable.",
                retry_at=exc.earliest_retry_at,
                metadata={
                    "attempted_models": list(exc.attempted_models),
                    "reasons_by_model": exc.details["reasons_by_model"],
                    "provider": exc.provider,
                },
            )
        except AiProviderError as exc:
            if operation_key and reservation_id:
                latency_ms = int((time.monotonic() - provider_started) * 1000) if "provider_started" in locals() else 0
                with self.session_factory() as session:
                    governance = AiGovernanceRepository(session)
                    AiBudgetService(governance, self.settings).reconcile(reservation_id, estimated_cost_micros)
                    governance.record_usage(tenant_id=tenant_id, operation_key=operation_key, values={
                        "asset_id": asset_id, "analysis_id": analysis_id, "job_id": job_id,
                        "provider": provider_name, "processing_mode": "single", "model": provider_model,
                        "metadata_profile": profile_name, "metadata_profile_version": profile_version,
                        "prompt_version": prompt_version, "input_units": 0, "output_units": 0,
                        "media_units": 0, "locally_estimated_cost_micros": estimated_cost_micros,
                        "currency": "USD", "latency_ms": latency_ms, "outcome": "provider_failed",
                        "retry_count": max(0, attempt_count - 1),
                    })
                    session.commit()
                AI_METRICS.increment("ai_failures", provider=provider_name, outcome=exc.code)
                AI_METRICS.latency(provider_name, "provider_failed", latency_ms)
            if exc.code == "analysis_cancelled":
                return await self._cancel(analysis_id)
            invalid_output = exc.code in {
                "gemini_invalid_json",
                "gemini_empty_response",
                "gemini_invalid_document",
                "gemini_invalid_response",
                "openai_invalid_json",
                "openai_empty_response",
                "openai_invalid_document",
            }
            retryable = exc.retryable and not (
                invalid_output
                and attempt_count >= self.settings.AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS
            )
            await self._record_failure(
                analysis_id, code=exc.code, message=str(exc), retryable=retryable,
                provider_metadata=exc.details or None,
            )
            return AiAnalysisOutcome(
                "retryable_failure" if retryable else "non_retryable_failure",
                exc.code,
                str(exc),
            )

    async def _cancel(self, analysis_id: str) -> AiAnalysisOutcome:
        await self._record_failure(
            analysis_id,
            code="analysis_cancelled",
            message="Analysis was cancelled.",
            retryable=True,
        )
        return AiAnalysisOutcome("cancelled", "analysis_cancelled", "Analysis was cancelled.")

    async def _record_failure(
        self,
        analysis_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
        validation_errors: list[dict] | None = None,
        provider_metadata: dict | None = None,
    ) -> None:
        with self.session_factory() as session:
            repository = AiMetadataRepository(session, self.validator)
            analysis = repository.get_analysis(analysis_id)
            if analysis.status != "completed":
                terminal = not retryable
                repository.fail_analysis(
                    analysis_id,
                    error_code=code,
                    error_message=message,
                    retryable=retryable,
                    validation_errors=validation_errors,
                    provider_metadata=provider_metadata,
                    terminal=terminal,
                )
                session.commit()
