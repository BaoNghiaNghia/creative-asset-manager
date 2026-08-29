from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.authorization.principal import (
    CurrentPrincipal, require_permission, require_principal_permission,
)
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_metadata.schema import (
    AiCapabilitiesResponse,
    EnqueueAssetAnalysisRequest,
    EnqueueAssetAnalysisResponse,
)
from app.modules.ai_metadata.selection import (
    AiProviderSelectionService,
    AiSelectionError,
)
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.providers.ai.factory import build_ai_provider_registry

router = APIRouter(prefix="/api/v1/admin/asset-analyses", tags=["asset-analyses"])
capabilities_router = APIRouter(prefix="/api/v1/admin/ai", tags=["ai"])
AI_ANALYSIS_RUN = require_permission("ai_analysis.run")


def _selection_error(exc: AiSelectionError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.post(
    "",
    response_model=EnqueueAssetAnalysisResponse,
    status_code=202,
)
def enqueue_asset_analysis(
    body: EnqueueAssetAnalysisRequest,
    principal: CurrentPrincipal = Depends(AI_ANALYSIS_RUN),
) -> EnqueueAssetAnalysisResponse:
    settings = get_settings()
    if body.force:
        require_principal_permission(principal, "ai_analysis.force")
    tenant_id = principal.active_tenant_id
    registry = build_ai_provider_registry(settings, session_factory=SessionLocal)
    try:
        with SessionLocal() as session:
            selection_service = AiProviderSelectionService(
                settings,
                registry,
                ProcessingPolicyRepository(session),
            )
            try:
                selection = selection_service.resolve(
                    tenant_id=tenant_id,
                    provider=body.ai_provider,
                    processing_mode=body.processing_mode,
                    model=body.ai_model,
                )
            except AiSelectionError as exc:
                raise _selection_error(exc) from exc

            repository = AiMetadataRepository(session)
            profile = repository.find_active_profile(
                tenant_id=tenant_id,
                profile_name=body.metadata_profile,
                profile_version=body.metadata_profile_version,
            )
            if profile is None:
                raise HTTPException(
                    status_code=404,
                    detail="Active metadata profile not found",
                )
            pipeline_version = (
                "single-asset-v1"
                if selection.processing_mode == "single"
                else "batch-asset-v1"
            )
            try:
                analysis = repository.create_analysis(
                    tenant_id=tenant_id,
                    asset_id=body.asset_id,
                    metadata_profile_id=profile.id,
                    prompt_version=f"profile-{profile.profile_version}",
                    pipeline_version=pipeline_version,
                    ai_provider=selection.provider,
                    ai_model=selection.model,
                    force=body.force,
                )
            except LookupError as exc:
                raise HTTPException(
                    status_code=404, detail="Asset not found"
                ) from exc

            if selection.processing_mode == "single":
                job_type = "asset_analyze"
                payload = {
                    "analysis_id": analysis.id,
                    "asset_id": analysis.asset_id,
                }
                idempotency_key = f"asset-analyze:{analysis.id}"
            else:
                job_type = "ai_batch_prepare"
                payload = {"analysis_ids": [analysis.id]}
                idempotency_key = f"ai-batch-prepare:{analysis.id}"
            job = ProcessingRepository(session).create_job(
                tenant_id=tenant_id,
                job_type=job_type,
                entity_type="asset_ai_analysis",
                entity_id=analysis.id,
                idempotency_key=idempotency_key,
                payload=payload,
                provider_key=selection.provider,
                provider_scope="ai",
            )
            if body.force:
                AiGovernanceRepository(session).event(
                    tenant_id, "forced_analysis", actor_id=principal.user_id,
                    details={"analysis_id": analysis.id, "provider": selection.provider, "model": selection.model, "mode": selection.processing_mode},
                )
            session.commit()
            return EnqueueAssetAnalysisResponse(
                analysis_id=analysis.id,
                job_id=job.id,
                provider=selection.provider,
                model=selection.model,
                processing_mode=selection.processing_mode,
                status="accepted",
            )
    finally:
        registry.close()


@capabilities_router.get(
    "/capabilities",
    response_model=AiCapabilitiesResponse,
)
def ai_capabilities(principal: CurrentPrincipal = Depends(AI_ANALYSIS_RUN)) -> AiCapabilitiesResponse:
    tenant_id = principal.active_tenant_id
    settings = get_settings()
    registry = build_ai_provider_registry(settings, session_factory=SessionLocal)
    try:
        with SessionLocal() as session:
            service = AiProviderSelectionService(
                settings,
                registry,
                ProcessingPolicyRepository(session),
            )
            result = service.capabilities(tenant_id)
            return AiCapabilitiesResponse.model_validate(result)
    finally:
        registry.close()
