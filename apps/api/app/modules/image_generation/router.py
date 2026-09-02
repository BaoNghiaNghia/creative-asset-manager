from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import get_db
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.image_generation.model import ImageGenerationRunModel
from app.modules.image_generation.schema import (
    GenerationError,
    ImageGenerationCapability,
    ImageGenerationResponse,
    SquareGenerationRequest,
)
from app.modules.image_generation.service import (
    ImageGenerationService,
    ImageGenerationServiceError,
    provider_capability,
)
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.ai.adobe_firefly import AdobeFireflySquareProvider, FireflyProviderError
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider

router = APIRouter(prefix="/api/v1/image-generations", tags=["image-generations"])
GENERATE = require_permission("assets.generate")
READ = require_permission("assets.read")


def _http_error(exc: ImageGenerationServiceError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _response(run: ImageGenerationRunModel) -> ImageGenerationResponse:
    error = (
        GenerationError(
            code=run.last_error_code or "image_generation_failed",
            message=run.last_error_message or "Image generation failed.",
        )
        if run.status == "failed"
        else None
    )
    return ImageGenerationResponse(
        id=run.id,
        source_asset_id=run.source_asset_id,
        status=run.status,
        provider=run.provider,
        model=run.provider_model,
        preservation_mode=run.preservation_mode,
        target_width=run.target_width,
        target_height=run.target_height,
        output_asset_id=run.output_asset_id,
        error=error,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get("/capabilities", response_model=ImageGenerationCapability)
def capabilities(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    return provider_capability(session, get_settings(), principal.active_tenant_id)


@router.post("/square", response_model=ImageGenerationResponse, status_code=202)
def create_square(
    request: SquareGenerationRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(GENERATE),
):
    try:
        created = ImageGenerationService(session, get_settings()).create(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            request=request,
        )
    except ImageGenerationServiceError as exc:
        raise _http_error(exc) from exc
    return _response(created.run)


@router.get("/{generation_id}", response_model=ImageGenerationResponse)
def get_generation(
    generation_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        run = ImageGenerationService(session, get_settings()).get(
            principal.active_tenant_id, generation_id
        )
    except ImageGenerationServiceError as exc:
        raise _http_error(exc) from exc
    return _response(run)


@router.post("/{generation_id}/cancel", response_model=ImageGenerationResponse)
async def cancel_generation(
    generation_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(GENERATE),
):
    service = ImageGenerationService(session, get_settings())
    try:
        run = service.get(principal.active_tenant_id, generation_id)
        if run.provider == "adobe_firefly" and run.provider_cancel_url and run.status in {"submitted", "running"}:
            adapter = AdobeFireflySquareProvider(
                client_id=get_settings().FIREFLY_SERVICES_CLIENT_ID,
                client_secret=get_settings().FIREFLY_SERVICES_CLIENT_SECRET,
            )
            try:
                await adapter.cancel(cancel_url=run.provider_cancel_url)
            except FireflyProviderError as exc:
                raise HTTPException(
                    status_code=503 if exc.retryable else 409,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            finally:
                await adapter.aclose()
        run = service.cancel(
            tenant_id=principal.active_tenant_id,
            generation_id=generation_id,
            actor_id=principal.actor_id,
        )
    except ImageGenerationServiceError as exc:
        raise _http_error(exc) from exc
    return _response(run)


@router.get("/{generation_id}/image")
async def get_generation_image(
    generation_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    try:
        run = ImageGenerationService(session, get_settings()).get(
            principal.active_tenant_id, generation_id
        )
    except ImageGenerationServiceError as exc:
        raise _http_error(exc) from exc
    if run.status != "completed" or not run.output_asset_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "image_generation_not_completed", "message": "Generated image is not available yet."},
        )
    stored = session.scalar(
        select(AssetStorageObjectModel).where(
            AssetStorageObjectModel.tenant_id == principal.active_tenant_id,
            AssetStorageObjectModel.asset_id == run.output_asset_id,
            AssetStorageObjectModel.status == "stored",
            AssetStorageObjectModel.remote_file_id.is_not(None),
        )
    )
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "image_generation_result_unavailable", "message": "Generated image is unavailable."},
        )
    provider = build_managed_storage_provider(get_settings())
    if isinstance(provider, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={"code": "managed_storage_unavailable", "message": "Managed Storage is unavailable."},
        )
    try:
        stream = await provider.open_asset(
            OpenStoredAssetInput(
                tenant_id=principal.active_tenant_id,
                asset_id=run.output_asset_id,
                remote_file_id=stored.remote_file_id,
                content_type="image/png",
            )
        )
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 404,
            detail={"code": "image_generation_result_unavailable", "message": "Generated image is unavailable."},
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, no-store", "Content-Disposition": f'inline; filename="generated-{generation_id}.image"'},
    )
