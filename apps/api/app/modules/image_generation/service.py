from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_operations.credential_model import CreativeAiCredentialModel
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.image_generation.model import ImageGenerationRunModel
from app.modules.image_generation.providers import GEMINI_IMAGE_MODEL
from app.modules.image_generation.repository import ImageGenerationRepository, ImageGenerationStateError
from app.modules.image_generation.schema import ImageGenerationCapability, ProviderCapability, SquareGenerationRequest
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository

SUPPORTED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_SOURCE_BYTES = 25 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class ImageGenerationServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class CreatedGeneration:
    run: ImageGenerationRunModel
    created: bool


def provider_capability(
    session: Session, settings: Settings, tenant_id: str
) -> ImageGenerationCapability:
    firefly_available = bool(
        settings.IMAGE_GENERATION_ENABLED
        and settings.FIREFLY_IMAGE_GENERATION_ENABLED
        and settings.FIREFLY_SERVICES_CLIENT_ID.strip()
        and settings.FIREFLY_SERVICES_CLIENT_SECRET.strip()
    )
    tenant_gemini = session.scalar(
        select(CreativeAiCredentialModel.id).where(
            CreativeAiCredentialModel.tenant_id == tenant_id,
            CreativeAiCredentialModel.provider == "gemini_image",
            CreativeAiCredentialModel.status == "active",
        )
    )
    gemini_available = bool(
        settings.IMAGE_GENERATION_ENABLED
        and settings.GEMINI_IMAGE_GENERATION_ENABLED
        and (tenant_gemini or (settings.GEMINI_IMAGE_API_KEY or "").strip())
    )
    return ImageGenerationCapability(
        enabled=settings.IMAGE_GENERATION_ENABLED,
        operations=["square_expand"] if settings.IMAGE_GENERATION_ENABLED else [],
        target_sizes=[1024, 2048],
        providers=[
            ProviderCapability(
                id="adobe_firefly",
                name="Adobe Firefly",
                available=firefly_available,
                preservation_mode="strict_expand",
                recommended=True,
            ),
            ProviderCapability(
                id="gemini",
                name="Gemini",
                available=gemini_available,
                preservation_mode="semantic_expand",
                recommended=False,
                model=GEMINI_IMAGE_MODEL,
            ),
        ],
    )


class ImageGenerationService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.runs = ImageGenerationRepository(session)
        self.jobs = ProcessingRepository(session, settings)

    def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: SquareGenerationRequest,
    ) -> CreatedGeneration:
        existing = self.runs.get_by_client_request(
            tenant_id, user_id, request.client_request_id
        )
        if existing is not None:
            return CreatedGeneration(existing, False)
        capability = provider_capability(self.session, self.settings, tenant_id)
        selected = next(item for item in capability.providers if item.id == request.provider)
        if not capability.enabled:
            raise ImageGenerationServiceError(
                "image_generation_disabled", "Image generation is disabled.", status_code=503
            )
        if not selected.available:
            raise ImageGenerationServiceError(
                f"{request.provider}_not_configured",
                "Selected image generation provider is unavailable.",
                status_code=503,
            )
        asset = self.session.scalar(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.id == request.source_asset_id,
            )
        )
        if asset is None:
            raise ImageGenerationServiceError("source_asset_not_found", "Source asset was not found.", status_code=404)
        query = (
            select(SourceAssetModel)
            .join(
                AssetSourceLinkModel,
                (AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
                & (AssetSourceLinkModel.tenant_id == SourceAssetModel.tenant_id),
            )
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                AssetSourceLinkModel.asset_id == asset.id,
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        )
        if request.source_source_asset_id:
            query = query.where(SourceAssetModel.id == request.source_source_asset_id)
        source = self.session.scalars(query.order_by(SourceAssetModel.created_at)).first()
        if source is None:
            raise ImageGenerationServiceError(
                "source_asset_unavailable",
                "No tenant-owned source file is available for this asset.",
                status_code=404,
            )
        mime = (source.mime_type or asset.mime_type or "").split(";", 1)[0].lower()
        if mime not in SUPPORTED_MIME_TYPES:
            raise ImageGenerationServiceError("source_image_unsupported", "Source image type is unsupported.")
        if source.size_bytes is not None and source.size_bytes > MAX_SOURCE_BYTES:
            raise ImageGenerationServiceError("source_image_too_large", "Source image exceeds the size limit.")
        preservation = "strict_expand" if request.provider == "adobe_firefly" else "semantic_expand"
        model = None if request.provider == "adobe_firefly" else GEMINI_IMAGE_MODEL
        run, created = self.runs.create_idempotent(
            tenant_id=tenant_id,
            source_asset_id=asset.id,
            source_source_asset_id=source.id,
            operation="square_expand",
            provider=request.provider,
            provider_model=model,
            preservation_mode=preservation,
            target_width=request.target_size,
            target_height=request.target_size,
            source_width=1,
            source_height=1,
            normalized_width=1,
            normalized_height=1,
            left=0,
            top=0,
            right=0,
            bottom=0,
            prompt=request.prompt.strip() if request.prompt and request.prompt.strip() else None,
            status="queued",
            client_request_id=request.client_request_id,
            created_by_user_id=user_id,
        )
        if created:
            self.jobs.create_job(
                tenant_id=tenant_id,
                job_type="image_generate",
                entity_type="image_generation_run",
                entity_id=run.id,
                idempotency_key=f"image-generate:{run.id}",
                payload={"image_generation_run_id": run.id},
                max_attempts=8,
                provider_key=request.provider,
                provider_scope="image_generation",
            )
        self.session.commit()
        if created:
            LOGGER.info(
                "image_generation_created",
                extra={
                    "generation_id": run.id,
                    "tenant_id": tenant_id,
                    "provider": run.provider,
                    "model": run.provider_model,
                    "target_size": run.target_width,
                    "status": run.status,
                },
            )
        return CreatedGeneration(run, created)

    def get(self, tenant_id: str, generation_id: str) -> ImageGenerationRunModel:
        run = self.runs.get(tenant_id, generation_id)
        if run is None:
            raise ImageGenerationServiceError("image_generation_not_found", "Image generation was not found.", status_code=404)
        return run

    def cancel(self, *, tenant_id: str, generation_id: str, actor_id: str) -> ImageGenerationRunModel:
        run = self.get(tenant_id, generation_id)
        if run.status == "cancelled":
            return run
        if run.status in {"completed", "failed"}:
            raise ImageGenerationServiceError("image_generation_terminal", "Completed or failed generation cannot be cancelled.", status_code=409)
        job = self.session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.entity_type == "image_generation_run",
                ProcessingJobModel.entity_id == run.id,
            )
        )
        if job is not None:
            if job.status in {"pending", "retry"}:
                self.jobs.cancel_unstarted_job(
                    tenant_id=tenant_id,
                    job_id=job.id,
                    actor_id=actor_id,
                    reason="Image generation cancellation requested.",
                )
            else:
                job.cancellation_requested = True
                job.cancel_requested_at = datetime.now(timezone.utc)
                job.cancel_requested_by = actor_id
                job.cancellation_reason = "Image generation cancellation requested."
        try:
            self.runs.cancel(run)
        except ImageGenerationStateError as exc:
            raise ImageGenerationServiceError("image_generation_terminal", "Generation cannot be cancelled.", status_code=409) from exc
        self.session.commit()
        return run
