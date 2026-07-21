from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.ai_batch.model import AiBatchItemModel, AiBatchJobModel
from app.modules.ai_batch.service import AiBatchService
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.orchestration import (
    AiAnalysisOrchestrationService,
    AiAnalysisRequestRepository,
    AnalysisRequestIdempotencyConflict,
)
from app.modules.ai_metadata.schema import (
    AnalysisRequestItemStatusResponse,
    AnalysisRequestStatusResponse,
    BulkAssetAnalysisAcceptedResponse,
    BulkAssetAnalysisItemResponse,
    BulkAssetAnalysisRequest,
    CancelAnalysisRequest,
)
from app.modules.ai_metadata.selection import (
    AiProviderSelectionService,
    AiSelectionError,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.auth import require_processing_admin
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.providers.ai.factory import build_ai_provider_registry
from app.providers.google.auth import get_session as get_google_session
from app.providers.microsoft.auth import get_session as get_microsoft_session
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider
from app.modules.external_ingestion.schema import validate_idempotency_key

router = APIRouter(
    prefix="/api/v1/admin/asset-analyses",
    tags=["asset-analyses"],
)


def _identity(request: Request) -> tuple[str, str]:
    session = get_google_session(request) or get_microsoft_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    actor = str(session.user.get("id") or session.user.get("email") or "")
    if not actor:
        raise HTTPException(
            status_code=403,
            detail="Authenticated account has no tenant identity",
        )
    return actor, actor


async def _validated_body(
    request: Request, *, maximum_bytes: int
) -> BulkAssetAnalysisRequest:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type.strip() != "application/json":
        raise HTTPException(
            status_code=415, detail="Content-Type must be application/json"
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > maximum_bytes:
                raise HTTPException(status_code=413, detail="Request payload is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    received = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        received += len(chunk)
        if received > maximum_bytes:
            raise HTTPException(status_code=413, detail="Request payload is too large")
        chunks.append(chunk)
    try:
        return BulkAssetAnalysisRequest.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        ) from exc


def _accepted_response(result) -> BulkAssetAnalysisAcceptedResponse:
    request = result.request
    return BulkAssetAnalysisAcceptedResponse(
        request_id=request.id,
        status="accepted",
        provider=request.ai_provider,
        model=request.ai_model,
        processing_mode=request.processing_mode,
        analysis_count=sum(item.analysis_id is not None for item in result.items),
        warning=request.warning,
        items=[
            BulkAssetAnalysisItemResponse(
                asset_id=item.requested_asset_id,
                acceptance_status=item.acceptance_status,
                analysis_id=item.analysis_id,
                job_id=item.processing_job_id,
                error_code=item.error_code,
                error_message=item.error_message,
            )
            for item in result.items
        ],
    )


def _status_response(
    session,
    *,
    tenant_id: str,
    request_id: str,
    include_provider_batch_id: bool,
) -> AnalysisRequestStatusResponse:
    repository = AiAnalysisRequestRepository(session)
    request = repository.get(tenant_id, request_id)
    items = repository.items(tenant_id, request_id)
    analysis_ids = [item.analysis_id for item in items if item.analysis_id]
    job_ids = [item.processing_job_id for item in items if item.processing_job_id]
    analyses = {
        value.id: value
        for value in session.scalars(
            select(AssetAiAnalysisModel).where(
                AssetAiAnalysisModel.tenant_id == tenant_id,
                AssetAiAnalysisModel.id.in_(analysis_ids),
            )
        )
    } if analysis_ids else {}
    jobs = {
        value.id: value
        for value in session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant_id,
                ProcessingJobModel.id.in_(job_ids),
            )
        )
    } if job_ids else {}
    batch_rows = list(session.execute(
        select(AiBatchItemModel.analysis_id, AiBatchJobModel)
        .join(
            AiBatchJobModel,
            (AiBatchJobModel.id == AiBatchItemModel.batch_job_id)
            & (AiBatchJobModel.tenant_id == AiBatchItemModel.tenant_id),
        )
        .where(
            AiBatchItemModel.tenant_id == tenant_id,
            AiBatchItemModel.analysis_id.in_(analysis_ids),
        )
    )) if analysis_ids else []
    batches_by_analysis = {
        analysis_id: batch for analysis_id, batch in batch_rows
    }
    response_items = []
    counts: Counter[str] = Counter()
    for item in items:
        analysis = analyses.get(item.analysis_id)
        job = jobs.get(item.processing_job_id)
        batch = batches_by_analysis.get(item.analysis_id)
        if request.status == "cancelled":
            processing_status = "cancelled"
        elif item.analysis_id is None:
            processing_status = "failed"
        elif analysis is not None and analysis.status == "completed":
            processing_status = "completed"
        elif (
            analysis is not None
            and analysis.status in {"failed", "budget_blocked"}
        ):
            processing_status = "failed"
        elif (
            (analysis is not None and analysis.status == "running")
            or (job is not None and job.status == "processing")
            or (
                batch is not None
                and batch.status
                in {"submitting", "submitted", "running", "importing", "ambiguous"}
            )
        ):
            processing_status = "running"
        else:
            processing_status = "queued"
        counts[processing_status] += 1
        response_items.append(AnalysisRequestItemStatusResponse(
            asset_id=item.requested_asset_id,
            acceptance_status=item.acceptance_status,
            analysis_id=item.analysis_id,
            job_id=item.processing_job_id,
            error_code=item.error_code or (
                analysis.last_error_code if analysis is not None else None
            ),
            error_message=item.error_message or (
                analysis.last_error_message if analysis is not None else None
            ),
            processing_status=processing_status,
            batch_id=batch.id if batch is not None else None,
            provider_batch_id=(
                batch.provider_batch_id
                if batch is not None and include_provider_batch_id
                else None
            ),
        ))
    batch_ids = {value.id for value in batches_by_analysis.values()}
    terminal = counts["completed"] + counts["failed"] + counts["cancelled"]
    if request.status == "cancelled":
        overall = "cancelled"
    elif counts["running"]:
        overall = "running"
    elif response_items and terminal == len(response_items):
        overall = "completed" if not counts["failed"] else "partial_failed"
    else:
        overall = "queued"
    return AnalysisRequestStatusResponse(
        request_id=request.id,
        status=overall,
        provider=request.ai_provider,
        model=request.ai_model,
        processing_mode=request.processing_mode,
        analysis_count=len(analysis_ids),
        batch_count=len(batch_ids),
        queued=counts["queued"],
        running=counts["running"],
        completed=counts["completed"],
        failed=counts["failed"],
        cancelled=counts["cancelled"],
        warning=request.warning,
        items=response_items,
        created_at=request.created_at,
        updated_at=request.updated_at,
        cancelled_at=request.cancelled_at,
    )


@router.post(
    "/bulk",
    response_model=BulkAssetAnalysisAcceptedResponse,
    status_code=202,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": BulkAssetAnalysisRequest.model_json_schema()
                }
            },
        }
    },
)
async def bulk_asset_analyses(
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> BulkAssetAnalysisAcceptedResponse:
    tenant_id, actor_id = _identity(request)
    settings = get_settings()
    try:
        key = validate_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    body = await _validated_body(
        request, maximum_bytes=settings.AI_ANALYSIS_BULK_MAX_PAYLOAD_BYTES
    )
    if len(body.asset_ids) > settings.AI_ANALYSIS_BULK_MAX_ITEMS:
        raise HTTPException(status_code=413, detail="Too many assets in bulk request")

    registry = build_ai_provider_registry(settings)
    try:
        with SessionLocal() as session:
            selection_service = AiProviderSelectionService(
                settings,
                registry,
                ProcessingPolicyRepository(session),
            )
            selection = None
            selection_error = None
            try:
                selection = selection_service.resolve(
                    tenant_id=tenant_id,
                    provider=body.ai_provider,
                    processing_mode=body.processing_mode,
                    model=body.ai_model,
                )
            except AiSelectionError as exc:
                if exc.code == "ai_model_not_allowed":
                    raise HTTPException(
                        status_code=exc.status_code,
                        detail={"code": exc.code, "message": str(exc)},
                    ) from exc
                selection_error = exc
            try:
                result = AiAnalysisOrchestrationService(
                    session, settings
                ).create_bulk(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    idempotency_key=key,
                    body=body,
                    selection=selection,
                    selection_error=selection_error,
                )
                session.commit()
            except AnalysisRequestIdempotencyConflict as exc:
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Idempotency-Key was already used with a different "
                        "request body"
                    ),
                ) from exc
            except LookupError as exc:
                session.rollback()
                raise HTTPException(
                    status_code=404, detail="Active metadata profile not found"
                ) from exc
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            return _accepted_response(result)
    finally:
        await registry.aclose()


@router.get(
    "/requests/{request_id}",
    response_model=AnalysisRequestStatusResponse,
)
def analysis_request_status(
    request_id: str,
    request: Request,
    include_provider_batch_id: bool = Query(False),
) -> AnalysisRequestStatusResponse:
    tenant_id, _actor_id = _identity(request)
    if include_provider_batch_id:
        admin = require_processing_admin(request)
        admin.authorize_tenant(tenant_id)
    with SessionLocal() as session:
        try:
            return _status_response(
                session,
                tenant_id=tenant_id,
                request_id=request_id,
                include_provider_batch_id=include_provider_batch_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Analysis request not found") from exc


@router.post(
    "/requests/{request_id}/cancel",
    response_model=AnalysisRequestStatusResponse,
)
async def cancel_analysis_request(
    request_id: str,
    body: CancelAnalysisRequest,
    request: Request,
) -> AnalysisRequestStatusResponse:
    tenant_id, actor_id = _identity(request)
    settings = get_settings()
    registry = build_ai_provider_registry(settings)
    try:
        with SessionLocal() as session:
            service = AiAnalysisOrchestrationService(session, settings)
            try:
                _analysis_request, batch_ids = service.cancel_queued(
                    tenant_id=tenant_id,
                    request_id=request_id,
                    actor_id=actor_id,
                    reason=body.reason,
                )
            except LookupError as exc:
                raise HTTPException(
                    status_code=404, detail="Analysis request not found"
                ) from exc
            session.commit()
            for batch_id in batch_ids:
                batch = session.get(AiBatchJobModel, batch_id)
                if batch is None or batch.status in {
                    "completed", "partial_failed", "failed", "expired", "cancelled"
                }:
                    continue
                if registry.get(batch.provider) is None:
                    batch.cancellation_requested = True
                    continue
                await AiBatchService(
                    session,
                    settings,
                    registry,
                    UnconfiguredAssetStorageProvider(),
                ).cancel(tenant_id=tenant_id, batch_id=batch_id)
            session.commit()
            return _status_response(
                session,
                tenant_id=tenant_id,
                request_id=request_id,
                include_provider_batch_id=False,
            )
    finally:
        await registry.aclose()
