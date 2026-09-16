from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.creative_pipeline.api_service import CreativePipelineApiService
from app.modules.creative_pipeline.constants import NodeRunStatus
from app.modules.creative_pipeline.model import (
    ArtifactModel, ListingTaskModel, NodeRunModel, PipelineRunModel, SourceGroupModel,
)
from app.modules.creative_pipeline.orchestrator import CreativePipelineOrchestrator, CreativePipelineStateError

router = APIRouter(prefix="/api/v1/creative-pipeline", tags=["creative-pipeline"])
READ = require_permission("assets.read")
MUTATE = require_permission("assets.generate")

def not_found():
    raise HTTPException(404, detail={"code": "creative_pipeline_not_found", "message": "Creative Pipeline resource was not found."})

def svc(session): return CreativePipelineApiService(session)

@router.get("/capabilities")
def capabilities(principal: CurrentPrincipal = Depends(READ)):
    return svc(None).capabilities()

@router.get("/groups")
def groups(session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    service = svc(session)
    rows = session.scalars(select(SourceGroupModel).where(SourceGroupModel.tenant_id == principal.active_tenant_id).order_by(SourceGroupModel.name, SourceGroupModel.id)).all()
    return {"items": [service.group_summary(row) for row in rows if service._scope_allows(principal, row)], "total": sum(service._scope_allows(principal, row) for row in rows)}

@router.get("/listings")
def listings(
    q: str | None = Query(None), source_group_id: str | None = Query(None),
    platform: str | None = Query(None), source_status: str | None = Query(None),
    run_status: str | None = Query(None), page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(READ),
):
    service = svc(session)
    statement = select(ListingTaskModel).where(ListingTaskModel.tenant_id == principal.active_tenant_id)
    if source_group_id: statement = statement.where(ListingTaskModel.source_group_id == source_group_id)
    if platform in {"etsy", "amazon"}: statement = statement.where(ListingTaskModel.platform == platform)
    if source_status in {"active", "missing_source", "archived"}: statement = statement.where(ListingTaskModel.status == source_status)
    rows = session.scalars(statement.order_by(ListingTaskModel.folder_name, ListingTaskModel.id)).all()
    result = []
    for row in rows:
        group = service._group(principal.active_tenant_id, row.source_group_id)
        if not service._scope_allows(principal, group, row): continue
        current = service._current_run(principal.active_tenant_id, row.id)
        if run_status:
            allowed = {"running": {"running"}, "failed": {"failed"}, "waiting": {"queued", "retrying", "blocked"}, "completed": {"completed"}, "cancelled": {"cancelled"}}.get(run_status)
            if allowed is not None and (current is None or current.status not in allowed): continue
        if q and q.lower() not in f"{row.listing_key} {row.folder_name}".lower(): continue
        result.append(service.listing_summary(row, group))
    total = len(result)
    start = (page - 1) * limit
    return {"items": result[start:start+limit], "page": page, "limit": limit, "total": total, "has_more": start + limit < total}

@router.get("/listings/{listing_id}")
def listing_detail(listing_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    service = svc(session); listing, group = service.require_listing(principal, listing_id)
    if listing is None: return not_found()
    return service.listing_summary(listing, group, include_detail=True)

def _branch_response(service, run):
    listing = service._listing(run.tenant_id, run.listing_task_id)
    return service.run_summary(run, listing)

@router.post("/listings/{listing_id}/start")
def start_initial_run(listing_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    service=svc(session); listing, group=service.require_listing(principal, listing_id)
    if listing is None: return not_found()
    run=service._current_run(principal.active_tenant_id, listing.id)
    if run is None or run.run_number != 1:
        raise HTTPException(409, detail={"code":"creative_pipeline_initial_run_unavailable","message":"Initial discovery run is unavailable."})
    if run.status != "queued":
        return _branch_response(service, run)
    if session.scalar(select(NodeRunModel.id).where(NodeRunModel.tenant_id==principal.active_tenant_id,NodeRunModel.pipeline_run_id==run.id)):
        return _branch_response(service, run)
    try:
        orch=CreativePipelineOrchestrator(session); orch.initialize_run(principal.active_tenant_id, run.id); orch.schedule_ready_nodes(principal.active_tenant_id, run.id); session.commit()
    except CreativePipelineStateError as exc:
        session.rollback(); raise HTTPException(409, detail={"code":"creative_pipeline_initial_run_unavailable","message":str(exc)}) from exc
    return _branch_response(service, run)

def _create_branch(listing_id, action, idempotency_key, session, principal):
    service=svc(session); listing, group=service.require_listing(principal, listing_id)
    if listing is None: return not_found()
    try:
        run=service.create_branch_run(principal, listing, action, idempotency_key)
        session.commit()
    except CreativePipelineStateError as exc:
        session.rollback(); raise HTTPException(409, detail={"code":"creative_pipeline_active_run_exists","message":"A pipeline run is already active."}) from exc
    except ValueError as exc:
        session.rollback(); code=str(exc)
        status=409 if code in {"creative_pipeline_idempotency_conflict","effective_input_unavailable","effective_idea_unavailable","effective_prompt_unavailable","invalid_idempotency_key","listing_source_unavailable"} else 400
        raise HTTPException(status, detail={"code":code,"message":"The requested pipeline branch is unavailable."}) from exc
    return _branch_response(service, run)

@router.post("/listings/{listing_id}/run")
def create_full_run(listing_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    if idempotency_key is None: raise HTTPException(400, detail={"code":"idempotency_key_required","message":"Idempotency-Key is required."})
    return _create_branch(listing_id, "run", idempotency_key, session, principal)

@router.post("/listings/{listing_id}/regenerate-idea")
def regenerate_idea(listing_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    if idempotency_key is None: raise HTTPException(400, detail={"code":"idempotency_key_required","message":"Idempotency-Key is required."})
    return _create_branch(listing_id, "regenerate-idea", idempotency_key, session, principal)

@router.post("/listings/{listing_id}/regenerate-prompt")
def regenerate_prompt(listing_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    if idempotency_key is None: raise HTTPException(400, detail={"code":"idempotency_key_required","message":"Idempotency-Key is required."})
    return _create_branch(listing_id, "regenerate-prompt", idempotency_key, session, principal)

@router.post("/listings/{listing_id}/generate-another-video")
def generate_another_video(listing_id: str, idempotency_key: str | None = Header(None, alias="Idempotency-Key"), session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    if idempotency_key is None: raise HTTPException(400, detail={"code":"idempotency_key_required","message":"Idempotency-Key is required."})
    return _create_branch(listing_id, "generate-another-video", idempotency_key, session, principal)

@router.get("/listings/{listing_id}/runs")
def listing_runs(listing_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    service = svc(session); listing, group = service.require_listing(principal, listing_id)
    if listing is None: return not_found()
    rows = session.scalars(select(PipelineRunModel).where(PipelineRunModel.tenant_id == principal.active_tenant_id, PipelineRunModel.listing_task_id == listing.id).order_by(PipelineRunModel.run_number.desc())).all()
    return {"items": [service.run_summary(row, listing) for row in rows]}

@router.get("/runs/{run_id}")
def run_detail(run_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    service = svc(session); run, listing, group = service.require_run(principal, run_id)
    if run is None: return not_found()
    payload = service.run_summary(run, listing)
    payload["listing"] = service.listing_summary(listing, group)
    return payload

@router.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    service = svc(session); run, listing, group = service.require_run(principal, run_id)
    if run is None: return not_found()
    return {"items": service.artifact_summaries(run)}

@router.get("/runs/{run_id}/generations")
def run_generations(run_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    service = svc(session); run, listing, group = service.require_run(principal, run_id)
    if run is None: return not_found()
    return {"items": service.generation_summaries(run)}

@router.get("/artifacts/{artifact_id}")
def artifact_detail(artifact_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(READ)):
    row = session.scalar(select(ArtifactModel).where(ArtifactModel.tenant_id == principal.active_tenant_id, ArtifactModel.id == artifact_id))
    if row is None: return not_found()
    service = svc(session); run, listing, group = service.require_run(principal, row.pipeline_run_id)
    if run is None: return not_found()
    return service.artifact_summary(row)

@router.post("/nodes/{node_id}/retry")
def retry_node(node_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    row = session.scalar(select(NodeRunModel).where(NodeRunModel.tenant_id == principal.active_tenant_id, NodeRunModel.id == node_id))
    if row is None: return not_found()
    service = svc(session); run, listing, group = service.require_run(principal, row.pipeline_run_id)
    if run is None: return not_found()
    if row.status != NodeRunStatus.RETRY_WAIT.value:
        code = "creative_terminal_node_retry_not_supported" if row.status in {"failed", "completed", "cancelled"} else "creative_node_retry_not_ready"
        raise HTTPException(409, detail={"code": code, "message": "Only retry_wait nodes can be retried."})
    try:
        CreativePipelineOrchestrator(session).retry_now(principal.active_tenant_id, row.id)
        session.commit()
    except CreativePipelineStateError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "creative_node_retry_not_ready", "message": str(exc)}) from exc
    return service.run_summary(run, listing)

@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    service = svc(session); run, listing, group = service.require_run(principal, run_id)
    if run is None: return not_found()
    try:
        CreativePipelineOrchestrator(session).cancel_run(principal.active_tenant_id, run.id, requested_by=principal.actor_id, reason="cancelled via API")
        session.commit()
    except CreativePipelineStateError as exc:
        session.rollback()
        raise HTTPException(409, detail={"code": "creative_run_cancel_failed", "message": str(exc)}) from exc
    return service.run_summary(run, listing)

@router.post("/groups/{group_id}/scan")
async def scan_group(group_id: str, session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(MUTATE)):
    service = svc(session); group = service.require_group(principal, group_id)
    if group is None: return not_found()
    try:
        result = await service.scan_group(principal, group)
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(503, detail={"code": "creative_group_scan_provider_failed", "message": "The source could not be scanned safely."}) from exc
    return {"group_id": group.id, "listings_created": result.listings_created, "listings_updated": result.listings_updated,
        "listings_missing": result.listings_missing, "pipeline_runs_created": result.pipeline_runs_created,
        "ignored_folders": result.ignored_folders, "errors": [{"code": error.code, "folder_id": error.folder_id} for error in result.errors]}
