from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.database import SessionLocal
from app.modules.ai_operations.export import EXPORT_COLUMNS, audit_export, csv_stream, export_rows
from app.modules.ai_operations.queries import AiOperationsRepository
from app.modules.ai_operations.schema import AiOperationsFilters
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin


router = APIRouter(prefix="/api/v1/admin/ai-operations", tags=["ai-operations"])
_VALID_MODES = {"single", "batch"}
_VALID_STATUSES = {
    "pending", "queued", "retrying", "running", "completed", "failed",
    "cancelled", "budget_blocked", "processing", "retry",
}


def _filters(
    admin: ProcessingAdmin,
    tenant_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
    provider: str | None,
    model: str | None,
    processing_mode: str | None,
    metadata_profile: str | None,
    status: str | None,
    source_provider: str | None,
) -> AiOperationsFilters:
    target_tenant = tenant_id or admin.own_tenant_id
    admin.authorize_tenant(target_tenant)
    end = to_at or datetime.now(timezone.utc)
    start = from_at or (end - timedelta(days=7))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start >= end:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_date_range", "message": "from must be before to"},
        )
    if end - start > timedelta(days=90):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "date_range_too_large",
                "message": "Interactive date range cannot exceed 90 days",
            },
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
    admin: ProcessingAdmin = Depends(require_processing_admin),
    tenant_id: str | None = Query(default=None),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    provider: str | None = Query(default=None, max_length=100),
    model: str | None = Query(default=None, max_length=255),
    processing_mode: str | None = Query(default=None),
    metadata_profile: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None),
    source_provider: str | None = Query(default=None, max_length=32),
) -> AiOperationsFilters:
    return _filters(
        admin, tenant_id, from_at, to_at, provider, model,
        processing_mode, metadata_profile, status, source_provider,
    )


def _read(filters: AiOperationsFilters, operation):
    with SessionLocal() as session:
        return operation(AiOperationsRepository(session), filters)


@router.get("/summary")
def summary(filters: AiOperationsFilters = Depends(common_filters)):
    return _read(filters, lambda repository, value: repository.summary(value))


@router.get("/daily")
def daily(filters: AiOperationsFilters = Depends(common_filters)):
    return {
        "period": {"from": filters.from_at, "to": filters.to_at},
        "items": _read(filters, lambda repository, value: repository.daily(value)),
    }


@router.get("/providers")
def providers(filters: AiOperationsFilters = Depends(common_filters)):
    return {
        "period": {"from": filters.from_at, "to": filters.to_at},
        "items": _read(filters, lambda repository, value: repository.providers(value)),
    }


@router.get("/failures")
def failures(filters: AiOperationsFilters = Depends(common_filters)):
    return {
        "period": {"from": filters.from_at, "to": filters.to_at},
        "items": _read(filters, lambda repository, value: repository.failures(value)),
    }


@router.get("/jobs")
def jobs(
    filters: AiOperationsFilters = Depends(common_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return _read(
        filters,
        lambda repository, value: repository.jobs(value, page=page, page_size=page_size),
    )


@router.get("/usage")
def usage(
    filters: AiOperationsFilters = Depends(common_filters),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return _read(
        filters,
        lambda repository, value: repository.usage(value, page=page, page_size=page_size),
    )

@router.get("/exports/{export_type}.csv")
def export_csv(
    export_type: str,
    filters: AiOperationsFilters = Depends(common_filters),
    row_limit: int = Query(default=5_000, ge=1, le=10_000),
    admin: ProcessingAdmin = Depends(require_processing_admin),
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
            actor_id=admin.actor_id,
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