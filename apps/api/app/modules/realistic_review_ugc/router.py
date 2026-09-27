from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.realistic_review_ugc.model import RrugcCampaignModel, RrugcCandidateModel
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.schema import (
    CampaignCreatedResponse,
    CampaignCreateRequest,
    CampaignResponse,
    CandidateBatchRequest,
    CandidateBatchResponse,
    CandidateResponse,
    ImportResponse,
    ScoutHeartbeatRequest,
    ScoutTaskResponse,
)
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
    campaign_token_matches,
)
from app.modules.storage.provider_factory import build_managed_storage_provider


router = APIRouter(
    prefix="/api/v1/realistic-review-ugc",
    tags=["realistic-review-ugc"],
)
READ = require_permission("realistic_review_ugc.read")
RUN = require_permission("realistic_review_ugc.run")


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


def _campaign(repository: RrugcRepository, row: RrugcCampaignModel) -> CampaignResponse:
    counts = repository.campaign_counts(row.tenant_id, row.id)
    return CampaignResponse(
        id=row.id,
        name=row.name,
        query=row.query,
        target_count=row.target_count,
        max_scroll_batches=row.max_scroll_batches,
        auto_import=row.auto_import,
        status=row.status,
        scout_status=row.scout_status,
        scout_last_seen_at=row.scout_last_seen_at,
        discovered=sum(counts.values()),
        drive_ready=counts.get("drive_ready", 0),
        failed=counts.get("import_failed", 0),
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
        name=request.name,
        query=request.query,
        target_count=request.target_count,
        max_scroll_batches=request.max_scroll_batches,
        auto_import=request.auto_import,
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
    "/campaigns/{campaign_id}/candidates/{candidate_id}/import",
    response_model=ImportResponse,
)
async def import_candidate(
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
        row = await RrugcService(session).import_candidate(
            candidate=candidate,
            storage=build_managed_storage_provider(get_settings()),
        )
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
async def scout_candidates(
    campaign_id: str,
    request: CandidateBatchRequest,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db),
):
    row = _scout_campaign(campaign_id, authorization, session)
    service = RrugcService(session)
    try:
        candidates, created, existing = service.ingest_candidates(
            campaign=row,
            submissions=request.items,
        )
    except RrugcError as exc:
        raise _error(exc) from exc

    if row.auto_import:
        storage = build_managed_storage_provider(get_settings())
        for candidate in candidates:
            if candidate.status != "discovered":
                continue
            try:
                await service.import_candidate(candidate=candidate, storage=storage)
            except RrugcError:
                # The candidate retains a durable import_failed state; one bad
                # remote image must not discard the rest of a bounded Scout batch.
                continue

    repository = RrugcRepository(session)
    counts = repository.campaign_counts(row.tenant_id, row.id)
    progress = counts.get("drive_ready", 0) if row.auto_import else sum(counts.values())
    if progress >= row.target_count:
        row.status = "completed"
        row.scout_status = "ready"
        session.commit()

    return CandidateBatchResponse(
        created=created,
        existing=existing,
        items=[_candidate(candidate) for candidate in candidates],
    )
