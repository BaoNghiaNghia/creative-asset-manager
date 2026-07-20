from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import Settings
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
from app.modules.ai_metadata.validator import MetadataDocumentValidator
from app.modules.assets.model import AssetModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.storage.repository import ManagedStorageRepository


@dataclass(frozen=True, slots=True)
class AiAnalysisOutcome:
    status: Literal["completed", "retryable_failure", "non_retryable_failure", "cancelled"]
    error_code: str | None = None
    error_message: str | None = None


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
            with self.session_factory() as session:
                repository = AiMetadataRepository(session, self.validator)
                repository.set_stage(analysis_id, "validating")
                session.commit()

            validation = self.validator.validate(result.metadata, json_schema=schema)
            if not validation.valid:
                errors = [
                    {
                        "code": item.code,
                        "message": item.message,
                        "path": list(item.path),
                        "limit": item.limit,
                        "actual": item.actual,
                    }
                    for item in validation.errors
                ]
                retryable = attempt_count < self.settings.AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS
                await self._record_failure(
                    analysis_id,
                    code="metadata_validation_failed",
                    message="AI metadata failed safety or profile validation.",
                    retryable=retryable,
                    validation_errors=errors,
                )
                return AiAnalysisOutcome(
                    "retryable_failure" if retryable else "non_retryable_failure",
                    "metadata_validation_failed",
                    "AI metadata failed safety or profile validation.",
                )
            metadata = validation.document or {}
            projection_result = self.projection_builder.build(metadata, search_config)
            projection = projection_result.projection.to_document()
            encoded = json.dumps(
                projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            projection_checksum = hashlib.sha256(encoded).hexdigest()

            with self.session_factory() as session:
                repository = AiMetadataRepository(session, self.validator)
                current = repository.get_analysis(analysis_id)
                if current.status == "completed":
                    return AiAnalysisOutcome("completed")
                repository.set_stage(analysis_id, "projection_ready")
                completed = repository.complete_analysis(
                    analysis_id=analysis_id,
                    metadata=metadata,
                    raw_response=result.raw_response,
                    store_raw_response=self.settings.AI_STORE_RAW_RESPONSE_ENABLED,
                    search_projection=projection,
                    search_projection_version=projection_result.projection_version,
                    projection_checksum=projection_checksum,
                    provider_request_id=result.provider_request_id,
                    usage=result.usage,
                    provider_metadata=result.provider_metadata,
                    ai_model=result.model,
                )
                ProcessingRepository(session).create_job(
                    tenant_id=tenant_id,
                    job_type="asset_index",
                    entity_type="asset",
                    entity_id=completed.asset_id,
                    idempotency_key=(
                        f"asset-index:{completed.id}:"
                        f"{projection_result.projection_version}:{projection_checksum}"
                    ),
                    provider_key="elasticsearch",
                    provider_scope="search",
                    payload={
                        "asset_id": completed.asset_id,
                        "analysis_id": completed.id,
                        "projection_version": projection_result.projection_version,
                    },
                )
                session.commit()
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
        except AiProviderError as exc:
            if exc.code == "analysis_cancelled":
                return await self._cancel(analysis_id)
            invalid_output = exc.code in {
                "gemini_invalid_json",
                "gemini_empty_response",
                "gemini_invalid_document",
                "gemini_invalid_response",
            }
            retryable = exc.retryable and not (
                invalid_output
                and attempt_count >= self.settings.AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS
            )
            await self._record_failure(
                analysis_id, code=exc.code, message=str(exc), retryable=retryable
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
                    terminal=terminal,
                )
                session.commit()
