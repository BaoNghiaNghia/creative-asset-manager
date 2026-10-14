"""GatorHats Products Stage 3: verified Stage 2 colorways -> Seedance video jobs.

Reuses the native video-generation queue; never submits unverified URLs or
arbitrary user-supplied Drive identifiers to the provider.
"""
from __future__ import annotations

import hashlib
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError
from app.modules.assets.model import AssetModel
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel, RrugcSourcePlanModel
from app.modules.video_generation.model import VideoGenerationReferenceModel, VideoGenerationRunModel
from app.modules.video_generation.schema import VideoGenerationRequest
from app.modules.video_generation.service import Service as VideoService, Error as VideoError, enabled as video_enabled, MAX, TOTAL, MIMES

router = APIRouter()
READ = require_permission("assets.read")
GENERATE = require_permission("assets.generate")


class ProductsVideoCreate(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)
    colorway_job_ids: list[str] = Field(min_length=1, max_length=8)
    prompt: str = Field(min_length=1, max_length=4000)
    aspect_ratio: str = Field(pattern=r"^(16:9|9:16|1:1|4:3|3:4)$")
    duration_seconds: int = Field(ge=10, le=30)


def _error(code: str, message: str, status: int = 409) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _response(run) -> dict:
    return {
        "id": run.id, "status": run.status, "provider": run.provider,
        "model": run.provider_model, "prompt": run.prompt,
        "aspect_ratio": run.aspect_ratio, "duration_seconds": run.duration_seconds,
        "output_asset_id": run.output_asset_id,
        "error": {"code": run.last_error_code, "message": run.last_error_message} if run.last_error_code else None,
        "created_at": run.created_at, "submitted_at": run.submitted_at,
        "completed_at": run.completed_at,
    }


@router.post("/products-stage3/videos", status_code=202)
async def create_products_stage3_video(
    payload: ProductsVideoCreate,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(GENERATE),
):
    settings = get_settings()
    tenant_id = principal.active_tenant_id
    if not video_enabled(settings, tenant_id):
        raise _error("seedance_not_enabled", "Seedance 2.5 video generation is not enabled for this tenant.", 503)
    if payload.duration_seconds not in (10, 15, 30):
        raise _error("invalid_video_duration", "Supported durations are 10, 15 and 30 seconds.", 422)
    if len(payload.colorway_job_ids) != len(set(payload.colorway_job_ids)):
        raise _error("duplicate_video_references", "Select each photo once.", 422)
    # Reject foreign-tenant, stale design revisions, unfinished and Reviews jobs.
    jobs = list(session.scalars(select(RrugcColorwayJobModel).join(
        RrugcSourcePlanModel,
        (RrugcSourcePlanModel.id == RrugcColorwayJobModel.source_plan_id)
        & (RrugcSourcePlanModel.tenant_id == tenant_id),
    ).where(
        RrugcColorwayJobModel.tenant_id == tenant_id,
        RrugcColorwayJobModel.id.in_(payload.colorway_job_ids),
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        RrugcSourcePlanModel.source_revision == RrugcColorwayJobModel.source_revision,
        RrugcSourcePlanModel.status != "missing",
        RrugcColorwayJobModel.status == "completed",
        RrugcColorwayJobModel.output_remote_file_id.is_not(None),
    )))
    if len(jobs) != len(payload.colorway_job_ids):
        raise _error("stage2_photos_not_ready", "Select only completed Stage 2 Products photos.", 422)
    by_id = {job.id: job for job in jobs}
    storage = build_managed_storage_provider(settings)
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise _error("managed_storage_unavailable", "Managed Storage is not connected.", 503)
    provider = getattr(storage, "provider_name", "google_drive")
    asset_ids: list[str] = []
    total_bytes = 0
    try:
        for job_id in payload.colorway_job_ids:
            job = by_id[job_id]
            mime = (job.output_content_type or "").split(";", 1)[0].lower()
            if mime not in MIMES or (job.output_size_bytes or 0) > MAX:
                raise _error("video_reference_invalid", "A selected photo exceeds the supported image format or size.", 422)
            remote_id = job.output_remote_file_id
            existing = session.scalar(select(AssetStorageObjectModel).where(
                AssetStorageObjectModel.tenant_id == tenant_id,
                AssetStorageObjectModel.storage_provider == provider,
                AssetStorageObjectModel.remote_file_id == remote_id,
                AssetStorageObjectModel.status == "stored",
            ))
            # Always validate the actual bytes, not just a historic metadata field.
            stream = await storage.open_asset(OpenStoredAssetInput(
                tenant_id=tenant_id, asset_id="rrugc-colorway:" + job.id,
                remote_file_id=remote_id, content_type=mime, size_bytes=job.output_size_bytes,
            ))
            data = bytearray()
            try:
                async for chunk in stream.body:
                    data.extend(chunk)
                    if len(data) > MAX:
                        raise _error("video_reference_too_large", "An image exceeds 15 MB.", 422)
            finally:
                await stream.close()
            total_bytes += len(data)
            if total_bytes > TOTAL:
                raise _error("video_references_too_large", "Selected images exceed 30 MB.", 422)
            try:
                with Image.open(BytesIO(data)) as im:
                    im.verify()
                    detected = Image.MIME.get(im.format or "", "").lower()
            except Exception as exc:
                raise _error("video_reference_invalid", "A Stage 2 photo is not a valid image.", 422) from exc
            if detected != mime:
                raise _error("video_reference_invalid", "Stage 2 photo MIME does not match its content.", 422)
            digest = hashlib.sha256(data).hexdigest()
            if existing is not None:
                asset = session.get(AssetModel, existing.asset_id)
                if asset is None or asset.content_hash != digest:
                    raise _error("video_reference_mismatch", "Stored image changed since it was registered.", 409)
            else:
                asset = session.scalar(select(AssetModel).where(
                    AssetModel.tenant_id == tenant_id, AssetModel.content_hash == digest,
                ))
                if asset is None:
                    asset = AssetModel(tenant_id=tenant_id, content_hash=digest, mime_type=mime, size_bytes=len(data))
                    session.add(asset)
                    session.flush()
                canonical = session.scalar(select(AssetStorageObjectModel).where(
                    AssetStorageObjectModel.tenant_id == tenant_id,
                    AssetStorageObjectModel.asset_id == asset.id,
                    AssetStorageObjectModel.storage_provider == provider,
                    AssetStorageObjectModel.status == "stored",
                ))
                if canonical is None:
                    session.add(AssetStorageObjectModel(
                        tenant_id=tenant_id, asset_id=asset.id, content_hash=digest,
                        storage_provider=provider, storage_class="durable",
                        status="stored", remote_file_id=remote_id,
                    ))
                    session.flush()
            asset_ids.append(asset.id)
        video_request = VideoGenerationRequest(
            client_request_id=payload.client_request_id,
            prompt=payload.prompt.strip(), model="seedance-2.5",
            aspect_ratio=payload.aspect_ratio,
            duration_seconds=payload.duration_seconds,
            reference_asset_ids=asset_ids,
        )
        result = VideoService(session, settings).create(tenant_id, principal.user_id, video_request)
        return _response(result)
    except HTTPException:
        session.rollback()
        raise
    except VideoError as exc:
        session.rollback()
        raise _error(exc.code, str(exc), exc.status_code) from exc
    except StorageProviderError as exc:
        session.rollback()
        raise _error(exc.code or "stage2_photo_unavailable", "Stage 2 image storage is unavailable.", 503) from exc
    except Exception:
        session.rollback()
        raise


@router.get("/products-stage3/videos")
def list_products_stage3_videos(
    source_plan_id: str = Query(min_length=1, max_length=64),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    tenant_id = principal.active_tenant_id
    valid_plan = session.scalar(select(RrugcSourcePlanModel.id).where(
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.id == source_plan_id,
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
    ))
    if valid_plan is None:
        raise _error("products_design_not_found", "Products design not found.", 404)
    # The reference is durable across page reloads; join by original stored file.
    runs = list(session.scalars(select(VideoGenerationRunModel).join(
        VideoGenerationReferenceModel,
        (VideoGenerationReferenceModel.run_id == VideoGenerationRunModel.id)
        & (VideoGenerationReferenceModel.tenant_id == tenant_id),
    ).join(
        AssetStorageObjectModel,
        (AssetStorageObjectModel.asset_id == VideoGenerationReferenceModel.asset_id)
        & (AssetStorageObjectModel.tenant_id == tenant_id),
    ).join(
        RrugcColorwayJobModel,
        (RrugcColorwayJobModel.output_remote_file_id == AssetStorageObjectModel.remote_file_id)
        & (RrugcColorwayJobModel.tenant_id == tenant_id),
    ).where(
        VideoGenerationRunModel.tenant_id == tenant_id,
        RrugcColorwayJobModel.source_plan_id == source_plan_id,
    ).distinct().order_by(VideoGenerationRunModel.created_at.desc()).limit(50)))
    return [_response(run) for run in runs]
