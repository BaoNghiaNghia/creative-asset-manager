from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_operations.cache import (
    cached_ai_operations_async,
    cached_ai_operations_read,
    filters_cache_key,
)
from app.modules.ai_operations.export import EXPORT_COLUMNS, audit_export, csv_stream, export_rows
from app.modules.ai_operations.queries import AiOperationsRepository
from app.modules.ai_operations.pipeline import PipelineOperationsRepository
from app.modules.ai_operations.media_dashboard import MediaDashboardService
from app.modules.ai_operations.coverage import SearchCoverageSummaryService
from app.modules.ai_operations.schema import AiOperationsFilters, ManagedStorageCleanupRequest, ManagedStorageSelfIngestionRepairRequest, SearchCoverageAuditRequest, SearchCoverageRepairRequest
from app.modules.authorization.principal import CurrentPrincipal, require_permission, require_tenant_scope
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.search.coverage_audit import SearchV3CoverageAudit, SearchV3CoverageRepair
from app.modules.storage.managed_cleanup import ManagedStorageCleanupService
from app.modules.storage.provider_factory import build_managed_storage_provider
from app.modules.storage.self_ingestion_repair import ManagedStorageSelfIngestionRepairService


router = APIRouter(prefix="/api/v1/admin/ai-operations", tags=["ai-operations"])
AI_OPERATIONS_READ = require_permission("ai_operations.read")
_VALID_MODES = {"single", "batch"}
_VALID_STATUSES = {
    "pending", "queued", "retrying", "running", "completed", "failed",
    "cancelled", "budget_blocked", "processing", "retry", "waiting",
}


def _filters(
    principal: CurrentPrincipal,
    tenant_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
    provider: str | None,
    model: str | None,
    processing_mode: str | None,
    metadata_profile: str | None,
    status: str | None,
    source_provider: str | None,
    date_range: str | None = None,
) -> AiOperationsFilters:
    target_tenant = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target_tenant)
    end = to_at or datetime.now(timezone.utc)
    if date_range == "all":
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        start = from_at or (end - timedelta(days=90))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start >= end:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_date_range", "message": "from must be before to"},
        )
    if processing_mode and processing_mode not in _VALID_MODES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_processing_mode", "message": "processing_mode must be single or batch"},
        )
    if status and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_status", "message": "Unsupported status filter"},
        )
    return AiOperationsFilters(
        tenant_id=target_tenant,
        from_at=start.astimezone(timezone.utc),
        to_at=end.astimezone(timezone.utc),
        provider=provider,
        model=model,
        processing_mode=processing_mode,
        metadata_profile=metadata_profile,
        status=status,
        source_provider=source_provider,
    )


def common_filters(
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
    tenant_id: str | None = Query(default=None),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    provider: str | None = Query(default=None, max_length=100),
    model: str | None = Query(default=None, max_length=255),
    processing_mode: str | None = Query(default=None),
    metadata_profile: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None),
    source_provider: str | None = Query(default=None, max_length=32),
    date_range: str | None = Query(default=None, alias="range"),
) -> AiOperationsFilters:
    return _filters(
        principal, tenant_id, from_at, to_at, provider, model,
        processing_mode, metadata_profile, status, source_provider, date_range,
    )


def _read(filters: AiOperationsFilters, operation):
    with SessionLocal() as session:
        return operation(AiOperationsRepository(session), filters)


def _cached_read(
    name: str,
    filters: AiOperationsFilters,
    operation,
    *extra: object,
):
    return cached_ai_operations_read(
        name,
        filters_cache_key(filters) + tuple(extra),
        lambda: _read(filters, operation),
    )


def _projection_version() -> str:
    return "search-projection-v1"


def _v3_index():
    settings = get_settings()
    if not settings.ELASTICSEARCH_URL:
        raise HTTPException(status_code=503, detail={"code": "search_unavailable", "message": "Elasticsearch is not configured"})
    from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
    return ElasticsearchV3Index(ElasticsearchV3Config(
        base_url=settings.ELASTICSEARCH_URL,
        index_prefix=settings.ELASTICSEARCH_INDEX_PREFIX,
        index_generation="v3",
    ))


def _audit_details(result, verify_elasticsearch: bool) -> dict:
    document = result.to_document()
    return {
        "verify_elasticsearch": verify_elasticsearch,
        "database_indexed_document_missing": document["document_missing"],
        "scanned": document["scanned"],
        "projection_missing": document["projection_missing"],
        "projection_stale": document["projection_stale"],
        "index_job_missing": document["index_job_missing"],
        "index_job_failed": document["index_job_failed"],
    }


@router.get("/managed-storage/cleanup/preview")
async def preview_managed_storage_cleanup(
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: CurrentPrincipal = Depends(require_permission("ai_provider.configure")),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    settings = get_settings()
    result = await ManagedStorageCleanupService(
        SessionLocal, settings, build_managed_storage_provider(settings)
    ).execute(tenant_id=target, limit=limit, dry_run=True)
    return result.document()


@router.post("/managed-storage/cleanup")
async def cleanup_managed_storage(
    body: ManagedStorageCleanupRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_permission("ai_provider.configure")),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    settings = get_settings()
    result = await ManagedStorageCleanupService(
        SessionLocal, settings, build_managed_storage_provider(settings)
    ).execute(tenant_id=target, limit=body.limit, dry_run=body.dry_run)
    return result.document()


@router.get("/managed-storage/self-ingestion/preview")
async def preview_managed_storage_self_ingestion_repair(
    tenant_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: CurrentPrincipal = Depends(require_permission("ai_provider.configure")),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    result = await ManagedStorageSelfIngestionRepairService(
        SessionLocal, get_settings()
    ).execute(tenant_id=target, limit=limit, dry_run=True)
    return result.document()


@router.post("/managed-storage/self-ingestion/repair")
async def repair_managed_storage_self_ingestion(
    body: ManagedStorageSelfIngestionRepairRequest,
    tenant_id: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_permission("ai_provider.configure")),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    result = await ManagedStorageSelfIngestionRepairService(
        SessionLocal, get_settings()
    ).execute(tenant_id=target, limit=body.limit, dry_run=body.dry_run)
    return result.document()


@router.get("/coverage")
def coverage(
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
    tenant_id: str | None = Query(default=None),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    with SessionLocal() as session:
        return SearchCoverageSummaryService(session, projection_version=_projection_version()).summary(tenant_id=target)


@router.post("/coverage/audit")
def run_coverage_audit(
    request: SearchCoverageAuditRequest,
    principal: CurrentPrincipal = Depends(require_permission("search.rebuild")),
    tenant_id: str | None = Query(default=None),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    with SessionLocal() as session:
        index = _v3_index() if request.verify_elasticsearch else None
        try:
            result = asyncio.run(SearchV3CoverageAudit(
                session, projection_version=_projection_version(), index=index,
            ).run(tenant_id=target, page_size=request.limit, limit=request.limit, verify_elasticsearch=request.verify_elasticsearch))
        finally:
            if index is not None:
                asyncio.run(index.client.aclose())
        details = _audit_details(result, request.verify_elasticsearch)
        AiGovernanceRepository(session).event(
            target, "search_coverage_audit", actor_id=principal.user_id, details=details,
        )
        session.commit()
        return {"audit": result.to_document(), "last_audited_at": datetime.now(timezone.utc), "elasticsearch_verification_included": request.verify_elasticsearch}


@router.post("/coverage/repair")
def repair_coverage(
    request: SearchCoverageRepairRequest,
    principal: CurrentPrincipal = Depends(require_permission("search.rebuild")),
    tenant_id: str | None = Query(default=None),
):
    if not request.confirmed:
        raise HTTPException(status_code=422, detail={"code": "confirmation_required", "message": "Repair requires explicit confirmation"})
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    with SessionLocal() as session:
        index = _v3_index() if request.verify_elasticsearch else None
        try:
            result = asyncio.run(SearchV3CoverageRepair(
                session, projection_version=_projection_version(), index=index,
            ).repair(
                tenant_id=target, page_size=request.limit, limit=request.limit,
                verify_elasticsearch=request.verify_elasticsearch, apply=True,
                repair_projections=request.repair_projections, repair_indexes=request.repair_indexes,
            ))
        finally:
            if index is not None:
                asyncio.run(index.client.aclose())
        AiGovernanceRepository(session).event(
            target, "search_coverage_repair_requested", actor_id=principal.user_id,
            details={"limit": request.limit, "projection_jobs_created": result.projection_jobs_created, "index_jobs_created": result.index_jobs_created},
        )
        session.commit()
        return {"repair": result.to_document(), "progress": SearchCoverageSummaryService(session, projection_version=_projection_version()).repair_jobs(tenant_id=target)}


def _pipeline_snapshot(
    tenant_id: str, recent_page: int, recent_page_size: int
):
    with SessionLocal() as session:
        return PipelineOperationsRepository(session).snapshot(
            tenant_id,
            recent_page=recent_page,
            recent_page_size=recent_page_size,
        )


@router.get("/pipeline")
def pipeline_summary(
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
    tenant_id: str | None = Query(default=None),
    recent_page: int = Query(default=1, ge=1),
    recent_page_size: int = Query(default=25, ge=1, le=100),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    return cached_ai_operations_read(
        "pipeline",
        (target, recent_page, recent_page_size),
        lambda: _pipeline_snapshot(target, recent_page, recent_page_size),
    )


@router.get("/media-dashboard")
async def media_dashboard(
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
    tenant_id: str | None = Query(default=None),
    video_page: int = Query(default=1, ge=1),
    video_page_size: int = Query(default=25, ge=1, le=100),
):
    target = tenant_id or principal.active_tenant_id
    require_tenant_scope(principal, target)
    async def load():
        with SessionLocal() as session:
            return await MediaDashboardService(
                session, get_settings()
            ).snapshot(
                target,
                video_page=video_page,
                video_page_size=video_page_size,
            )

    return await cached_ai_operations_async(
        "media-dashboard",
        (target, video_page, video_page_size),
        load,
    )


@router.get("/summary")
def summary(filters: AiOperationsFilters = Depends(common_filters)):
    return _cached_read("summary", filters, lambda repository, value: repository.summary(value))


@router.get("/daily")
def daily(filters: AiOperationsFilters = Depends(common_filters)):
    return {
        "period": {"from": filters.from_at, "to": filters.to_at},
        "items": _cached_read("daily", filters, lambda repository, value: repository.daily(value)),
    }


@router.get("/providers")
def providers(filters: AiOperationsFilters = Depends(common_filters)):
    return {
        "period": {"from": filters.from_at, "to": filters.to_at},
        "items": _cached_read("providers", filters, lambda repository, value: repository.providers(value)),
    }


@router.get("/failures")
def failures(filters: AiOperationsFilters = Depends(common_filters)):
    return {
        "period": {"from": filters.from_at, "to": filters.to_at},
        "items": _cached_read("failures", filters, lambda repository, value: repository.failures(value)),
    }


@router.get("/jobs")
def jobs(
    filters: AiOperationsFilters = Depends(common_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return _cached_read(
        "jobs",
        filters,
        lambda repository, value: repository.jobs(
            value, page=page, page_size=page_size
        ),
        page,
        page_size,
    )


@router.get("/usage")
def usage(
    filters: AiOperationsFilters = Depends(common_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return _cached_read(
        "usage",
        filters,
        lambda repository, value: repository.usage(
            value, page=page, page_size=page_size
        ),
        page,
        page_size,
    )

@router.get("/exports/{export_type}.csv")
def export_csv(
    export_type: str,
    filters: AiOperationsFilters = Depends(common_filters),
    row_limit: int = Query(default=5_000, ge=1, le=10_000),
    principal: CurrentPrincipal = Depends(AI_OPERATIONS_READ),
):
    if export_type not in EXPORT_COLUMNS:
        raise HTTPException(
            status_code=404,
            detail={"code": "export_not_found", "message": "Unsupported export type"},
        )
    # Audit is durable before the stream begins. Only bounded filter metadata is
    # recorded; rows and sensitive provider fields are never copied to audit.
    with SessionLocal() as session:
        audit_export(
            session,
            actor_id=principal.user_id,
            filters=filters,
            export_type=export_type,
            row_limit=row_limit,
        )
        session.commit()

    def generate():
        with SessionLocal() as session:
            repository = AiOperationsRepository(session)
            rows = export_rows(
                repository,
                export_type=export_type,
                filters=filters,
                row_limit=row_limit,
            )
            yield from csv_stream(EXPORT_COLUMNS[export_type], rows)

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ai-operations-{export_type}.csv"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )