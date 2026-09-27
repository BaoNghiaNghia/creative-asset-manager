from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import get_db
from app.domain.providers.contracts import OpenStoredAssetInput, StorageProviderError
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcProductModel,
    RrugcProductReferenceModel,
)
from app.modules.realistic_review_ugc.product_registry import (
    PRODUCT_REFERENCE_MAX_BYTES,
    RrugcProductRegistry,
)
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import (
    AnalyzeResponse,
    CampaignCreatedResponse,
    CampaignCreateRequest,
    CampaignResponse,
    CandidateBatchRequest,
    CandidateBatchResponse,
    CandidateResponse,
    ImportResponse,
    ProductCreateRequest,
    ProductReferenceResponse,
    ProductReferenceView,
    ProductResponse,
    ProductUpdateRequest,
    ScoutHeartbeatRequest,
    ScoutTaskResponse,
)
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
    campaign_token_matches,
)
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


router = APIRouter(
    prefix="/api/v1/realistic-review-ugc",
    tags=["realistic-review-ugc"],
)
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")

ANALYSIS_APPROVED_STATUSES = {
    "approved",
    "import_queued",
    "importing",
    "drive_ready",
    "rejected_duplicate",
}
ANALYSIS_REJECTED_STATUSES = {
    "rejected_no_person",
    "rejected_head_ratio",
    "rejected_expression",
    "rejected_existing_headwear",
    "rejected_head_occlusion",
    "rejected_quality",
    "rejected_ai_risk",
    "rejected_context",
}


def _error(exc: RrugcError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _candidate(row: RrugcCandidateModel) -> CandidateResponse:
    return CandidateResponse.model_validate({
        "id": row.id,
        "campaign_id": row.campaign_id,
        "pin_url": row.pin_url,
        "image_url": row.image_url,
        "alt_text": row.alt_text,
        "status": row.status,
        "analysis_revision": row.analysis_revision,
        "import_revision": row.import_revision,
        "people_count": row.people_count,
        "primary_head_ratio": row.primary_head_ratio,
        "smile_score": row.smile_score,
        "head_visible": row.head_visible,
        "existing_headwear": row.existing_headwear,
        "head_occlusion": row.head_occlusion,
        "mobile_ugc_score": row.mobile_ugc_score,
        "quality_score": row.quality_score,
        "ai_risk_score": row.ai_risk_score,
        "product_fit_score": row.product_fit_score,
        "final_score": row.final_score,
        "reject_reason": row.reject_reason,
        "analyzer_provider": row.analyzer_provider,
        "analyzer_model": row.analyzer_model,
        "analyzer_version": row.analyzer_version,
        "analysis_summary": row.analysis_summary,
        "analyzed_at": row.analyzed_at,
        "content_hash": row.content_hash,
        "width": row.width,
        "height": row.height,
        "size_bytes": row.size_bytes,
        "remote_file_id": row.remote_file_id,
        "remote_folder_id": row.remote_folder_id,
        "web_url": row.web_url,
        "last_error_code": row.last_error_code,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    })


def _product_reference(row: RrugcProductReferenceModel) -> ProductReferenceResponse:
    return ProductReferenceResponse(
        id=row.id,
        product_id=row.product_id,
        view_type=row.view_type,
        version=row.version,
        status=row.status,
        content_hash=row.content_hash,
        original_filename=row.original_filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        width=row.width,
        height=row.height,
        image_format=row.image_format,
        remote_file_id=row.remote_file_id,
        remote_folder_id=row.remote_folder_id,
        web_url=row.web_url,
        reused_storage=row.reused_storage,
        created_at=row.created_at,
        archived_at=row.archived_at,
    )


def _product(repository: RrugcRepository, row: RrugcProductModel) -> ProductResponse:
    references = repository.list_product_references(row.tenant_id, row.id)
    views = sorted({reference.view_type for reference in references})
    return ProductResponse(
        id=row.id,
        sku=row.sku,
        name=row.name,
        product_type=row.product_type,
        color=row.color,
        material=row.material,
        crown_profile=row.crown_profile,
        crown_height_mm=row.crown_height_mm,
        brim_style=row.brim_style,
        brim_length_mm=row.brim_length_mm,
        circumference_mm=row.circumference_mm,
        logo_position=row.logo_position,
        fit_notes=row.fit_notes,
        revision=row.revision,
        status=row.status,
        reference_count=len(references),
        active_views=views,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
    )


def _require_product(
    repository: RrugcRepository, tenant_id: str, product_id: str
) -> RrugcProductModel:
    row = repository.get_product(tenant_id, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return row


async def _read_product_reference_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > PRODUCT_REFERENCE_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "product_reference_too_large",
                    "message": "Product reference image exceeds the 20 MB limit.",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _count_sum(counts: Counter, statuses: set[str]) -> int:
    return sum(int(counts.get(status, 0)) for status in statuses)


def _campaign(repository: RrugcRepository, row: RrugcCampaignModel) -> CampaignResponse:
    counts = repository.campaign_counts(row.tenant_id, row.id)
    approved = _count_sum(counts, ANALYSIS_APPROVED_STATUSES)
    rejected = _count_sum(counts, ANALYSIS_REJECTED_STATUSES)
    return CampaignResponse(
        id=row.id,
        name=row.name,
        query=row.query,
        target_count=row.target_count,
        max_scroll_batches=row.max_scroll_batches,
        auto_import=row.auto_import,
        min_head_ratio=row.min_head_ratio,
        max_head_ratio=row.max_head_ratio,
        min_smile_score=row.min_smile_score,
        max_head_occlusion=row.max_head_occlusion,
        max_ai_risk_score=row.max_ai_risk_score,
        min_quality_score=row.min_quality_score,
        min_ugc_score=row.min_ugc_score,
        min_product_fit_score=row.min_product_fit_score,
        require_head_visible=row.require_head_visible,
        reject_headwear=row.reject_headwear,
        status=row.status,
        scout_status=row.scout_status,
        scout_last_seen_at=row.scout_last_seen_at,
        discovered=sum(counts.values()),
        analysis_pending=counts.get("analysis_queued", 0),
        analyzing=counts.get("analyzing", 0),
        approved=approved,
        rejected=rejected,
        drive_ready=counts.get("drive_ready", 0),
        failed=counts.get("analysis_failed", 0) + counts.get("import_failed", 0),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _require_campaign(
    repository: RrugcRepository, tenant_id: str, campaign_id: str
) -> RrugcCampaignModel:
    row = repository.get_campaign(tenant_id, campaign_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return row


def _scout_campaign(
    campaign_id: str,
    authorization: str | None,
    session: Session,
) -> RrugcCampaignModel:
    row = RrugcRepository(session).get_campaign_unscoped(campaign_id)
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if row is None or not campaign_token_matches(row, token):
        raise HTTPException(status_code=401, detail="Invalid scout credentials")
    return row



@router.get("/products", response_model=list[ProductResponse])
def list_products(
    include_archived: bool = Query(False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return [
        _product(repository, row)
        for row in repository.list_products(
            principal.active_tenant_id,
            include_archived=include_archived,
        )
    ]


@router.post("/products", response_model=ProductResponse, status_code=201)
def create_product(
    request: ProductCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    try:
        row = RrugcProductRegistry(session).create_product(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            request=request,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _product(RrugcRepository(session), row)


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return _product(
        repository,
        _require_product(repository, principal.active_tenant_id, product_id),
    )


@router.patch("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: str,
    request: ProductUpdateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    product = _require_product(repository, principal.active_tenant_id, product_id)
    try:
        row = RrugcProductRegistry(session).update_product(
            product=product,
            request=request,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _product(repository, row)


@router.delete("/products/{product_id}", response_model=ProductResponse)
def archive_product(
    product_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    product = _require_product(repository, principal.active_tenant_id, product_id)
    row = RrugcProductRegistry(session).archive_product(product)
    return _product(repository, row)


@router.get(
    "/products/{product_id}/references",
    response_model=list[ProductReferenceResponse],
)
def list_product_references(
    product_id: str,
    include_archived: bool = Query(False),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    return [
        _product_reference(row)
        for row in repository.list_product_references(
            principal.active_tenant_id,
            product_id,
            include_archived=include_archived,
        )
    ]


@router.get(
    "/products/{product_id}/references/{reference_id}/image",
)
async def get_product_reference_image(
    product_id: str,
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    reference = repository.get_product_reference(
        principal.active_tenant_id,
        product_id,
        reference_id,
    )
    if reference is None or not reference.remote_file_id:
        raise HTTPException(status_code=404, detail="Product reference image not found")

    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    try:
        stream = await storage.open_asset(
            OpenStoredAssetInput(
                tenant_id=principal.active_tenant_id,
                asset_id=reference.id,
                remote_file_id=reference.remote_file_id,
                content_type=reference.content_type,
                size_bytes=reference.size_bytes,
            )
        )
    except StorageProviderError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 404,
            detail={
                "code": "product_reference_unavailable",
                "message": "Product reference image is unavailable.",
            },
        ) from exc
    return StreamingResponse(
        stream.body,
        media_type=stream.content_type,
        background=BackgroundTask(stream.close),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post(
    "/products/{product_id}/references",
    response_model=ProductReferenceResponse,
    status_code=201,
)
async def upload_product_reference(
    product_id: str,
    view_type: ProductReferenceView = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    content = await _read_product_reference_upload(file)
    storage = build_managed_storage_provider(get_settings())
    if isinstance(storage, UnconfiguredAssetStorageProvider):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "managed_storage_unavailable",
                "message": "Managed Google Drive is unavailable.",
            },
        )
    try:
        row = await RrugcProductRegistry(session).upload_reference(
            tenant_id=principal.active_tenant_id,
            user_id=principal.user_id,
            product_id=product_id,
            view_type=view_type,
            original_filename=file.filename,
            content=content,
            storage=storage,
        )
    except RrugcError as exc:
        raise _error(exc) from exc
    return _product_reference(row)


@router.delete(
    "/products/{product_id}/references/{reference_id}",
    response_model=ProductReferenceResponse,
)
def archive_product_reference(
    product_id: str,
    reference_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_product(repository, principal.active_tenant_id, product_id)
    reference = repository.get_product_reference(
        principal.active_tenant_id,
        product_id,
        reference_id,
    )
    if reference is None:
        raise HTTPException(status_code=404, detail="Product reference not found")
    row = RrugcProductRegistry(session).archive_reference(reference)
    return _product_reference(row)


@router.get("/campaigns", response_model=list[CampaignResponse])
def list_campaigns(
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return [_campaign(repository, row) for row in repository.list_campaigns(principal.active_tenant_id)]


@router.post("/campaigns", response_model=CampaignCreatedResponse, status_code=201)
def create_campaign(
    request: CampaignCreateRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    row, raw_token = RrugcService(session).create_campaign(
        tenant_id=principal.active_tenant_id,
        user_id=principal.user_id,
        **request.model_dump(),
    )
    response = _campaign(RrugcRepository(session), row).model_dump()
    return CampaignCreatedResponse(**response, scout_token=raw_token)


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    return _campaign(
        repository,
        _require_campaign(repository, principal.active_tenant_id, campaign_id),
    )


@router.get("/campaigns/{campaign_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(
    campaign_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    return [
        _candidate(row)
        for row in repository.list_candidates(
            principal.active_tenant_id, campaign_id, limit=limit, offset=offset
        )
    ]


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/analyze",
    response_model=AnalyzeResponse,
    status_code=202,
)
def analyze_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row = RrugcService(session).reanalyze_candidate(candidate)
    except RrugcError as exc:
        raise _error(exc) from exc
    return AnalyzeResponse(candidate=_candidate(row))


@router.post(
    "/campaigns/{campaign_id}/candidates/{candidate_id}/import",
    response_model=ImportResponse,
    status_code=202,
)
def import_candidate(
    campaign_id: str,
    candidate_id: str,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(RUN),
):
    repository = RrugcRepository(session)
    _require_campaign(repository, principal.active_tenant_id, campaign_id)
    candidate = repository.get_candidate(
        principal.active_tenant_id, campaign_id, candidate_id
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        row = RrugcService(session).queue_candidate_import(candidate)
    except RrugcError as exc:
        raise _error(exc) from exc
    return ImportResponse(candidate=_candidate(row))


@router.get("/scout/{campaign_id}/task", response_model=ScoutTaskResponse)
def scout_task(
    campaign_id: str,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    repository = RrugcRepository(session)
    counts = repository.campaign_counts(row.tenant_id, row.id)
    row.scout_last_seen_at = datetime.now(timezone.utc)
    row.scout_status = "ready"
    session.commit()
    return ScoutTaskResponse(
        campaign_id=row.id,
        query=row.query,
        target_count=row.target_count,
        max_scroll_batches=row.max_scroll_batches,
        auto_import=row.auto_import,
        status=row.status,
        discovered=sum(counts.values()),
        approved=_count_sum(counts, ANALYSIS_APPROVED_STATUSES),
        rejected=_count_sum(counts, ANALYSIS_REJECTED_STATUSES),
        analysis_pending=counts.get("analysis_queued", 0) + counts.get("analyzing", 0),
        drive_ready=counts.get("drive_ready", 0),
    )


@router.post("/scout/{campaign_id}/heartbeat", status_code=204)
def scout_heartbeat(
    campaign_id: str,
    request: ScoutHeartbeatRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    row.scout_status = request.status
    row.scout_last_seen_at = datetime.now(timezone.utc)
    session.commit()
    return None


@router.post(
    "/scout/{campaign_id}/candidates",
    response_model=CandidateBatchResponse,
)
def scout_candidates(
    campaign_id: str,
    request: CandidateBatchRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    if row.status != "running":
        raise HTTPException(status_code=409, detail="Campaign is not running")
    try:
        candidates, created, existing = RrugcService(session).ingest_candidates(
            campaign=row,
            submissions=request.items,
        )
    except RrugcError as exc:
        raise _error(exc) from exc

    return CandidateBatchResponse(
        created=created,
        existing=existing,
        items=[_candidate(candidate) for candidate in candidates],
    )
