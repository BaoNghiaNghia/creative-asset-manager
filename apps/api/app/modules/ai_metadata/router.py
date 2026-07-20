from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.schema import (
    EnqueueAssetAnalysisRequest,
    EnqueueAssetAnalysisResponse,
)
from app.modules.processing.repository import ProcessingRepository
from app.providers.google.auth import get_session as get_google_session
from app.providers.microsoft.auth import get_session as get_microsoft_session

router = APIRouter(prefix="/api/v1/admin/asset-analyses", tags=["asset-analyses"])


def _tenant_id(request: Request, provider: str) -> str:
    session = (
        get_microsoft_session(request)
        if provider == "sharepoint"
        else get_google_session(request)
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    value = session.user.get("id") or session.user.get("email")
    if not value:
        raise HTTPException(status_code=403, detail="Authenticated account has no tenant identity")
    return str(value)


@router.post(
    "",
    response_model=EnqueueAssetAnalysisResponse,
    status_code=202,
)
def enqueue_asset_analysis(
    body: EnqueueAssetAnalysisRequest,
    request: Request,
) -> EnqueueAssetAnalysisResponse:
    settings = get_settings()
    if not (
        settings.DYNAMIC_AI_METADATA_ENABLED
        and settings.AI_SINGLE_ANALYSIS_ENABLED
    ):
        raise HTTPException(status_code=404, detail="Asset analysis is not enabled")
    tenant_id = _tenant_id(request, body.source_provider)
    with SessionLocal() as session:
        repository = AiMetadataRepository(session)
        profile = repository.find_active_profile(
            tenant_id=tenant_id,
            profile_name=body.metadata_profile,
            profile_version=body.metadata_profile_version,
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="Active metadata profile not found")
        try:
            analysis = repository.create_analysis(
                tenant_id=tenant_id,
                asset_id=body.asset_id,
                metadata_profile_id=profile.id,
                prompt_version=f"profile-{profile.profile_version}",
                pipeline_version="single-asset-v1",
                ai_provider="gemini",
                ai_model=settings.GEMINI_MODEL,
                force=body.force,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Asset not found") from exc
        job = ProcessingRepository(session).create_job(
            tenant_id=tenant_id,
            job_type="asset_analyze",
            entity_type="asset_ai_analysis",
            entity_id=analysis.id,
            idempotency_key=f"asset-analyze:{analysis.id}",
            payload={"analysis_id": analysis.id, "asset_id": analysis.asset_id},
        )
        session.commit()
        return EnqueueAssetAnalysisResponse(
            analysis_id=analysis.id,
            job_id=job.id,
            status="accepted",
        )
