from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import ValidationError
from app.core.config import get_settings

from app.modules.external_ingestion.auth import ExternalApiContext, authenticate_external_api
from app.modules.external_ingestion.repository import IdempotencyConflictError
from app.modules.external_ingestion.schema import (
    MAX_INGESTION_PAYLOAD_BYTES,
    AssetIngestionAcceptedResponse,
    AssetIngestionItemResponse,
    AssetIngestionItemsResponse,
    AssetIngestionRequest,
    AssetIngestionStatusResponse,
    validate_idempotency_key,
)
from app.modules.external_ingestion.service import ExternalIngestionService
from app.modules.processing.repository import ProcessingRepository

router = APIRouter(prefix="/api/v1/asset-ingestions", tags=["external-ingestions"])


async def _validated_body(request: Request) -> AssetIngestionRequest:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_INGESTION_PAYLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Request payload is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_INGESTION_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Request payload is too large")
        chunks.append(chunk)
    try:
        return AssetIngestionRequest.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        ) from exc


def _status_response(context: ExternalApiContext, ingestion) -> AssetIngestionStatusResponse:
    counts = context.repository.status_counts(context.tenant_id, ingestion.id)
    return AssetIngestionStatusResponse(
        ingestion_id=ingestion.id,
        source_id=ingestion.external_source_id,
        status=ingestion.status,
        received=ingestion.received_count,
        queued=counts.get("queued", 0),
        processing=counts.get("processing", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        created_at=ingestion.created_at,
        updated_at=ingestion.updated_at,
        completed_at=ingestion.completed_at,
    )


@router.post(
    "",
    response_model=AssetIngestionAcceptedResponse,
    status_code=202,
    openapi_extra={
        "requestBody": {"required": True, "content": {"application/json": {"schema": AssetIngestionRequest.model_json_schema()}}}
    },
)
async def create_ingestion(
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    context: ExternalApiContext = Depends(authenticate_external_api),
):
    try:
        key = validate_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    body = await _validated_body(request)
    try:
        ingestion = ExternalIngestionService(
            context.repository,
            ProcessingRepository(context.repository.session),
            unified_pipeline_enabled=get_settings().UNIFIED_ASSET_INGESTION_ENABLED,
        ).create(
            credential=context.credential,
            idempotency_key=key,
            request=body,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key was already used with a different request body",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return AssetIngestionAcceptedResponse(
        ingestion_id=ingestion.id,
        status=ingestion.status,
        received=ingestion.received_count,
    )


@router.get("/{ingestion_id}", response_model=AssetIngestionStatusResponse)
def ingestion_status(
    ingestion_id: str,
    context: ExternalApiContext = Depends(authenticate_external_api),
):
    ingestion = context.repository.get_ingestion(
        tenant_id=context.tenant_id,
        external_source_id=context.source_id,
        ingestion_id=ingestion_id,
    )
    if ingestion is None:
        raise HTTPException(status_code=404, detail="Ingestion not found")
    return _status_response(context, ingestion)


@router.get("/{ingestion_id}/items", response_model=AssetIngestionItemsResponse)
def ingestion_items(
    ingestion_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    context: ExternalApiContext = Depends(authenticate_external_api),
):
    ingestion = context.repository.get_ingestion(
        tenant_id=context.tenant_id,
        external_source_id=context.source_id,
        ingestion_id=ingestion_id,
    )
    if ingestion is None:
        raise HTTPException(status_code=404, detail="Ingestion not found")
    items = context.repository.list_items(
        tenant_id=context.tenant_id,
        ingestion_id=ingestion.id,
        limit=limit,
        offset=offset,
    )
    return AssetIngestionItemsResponse(
        ingestion_id=ingestion.id,
        total=ingestion.received_count,
        offset=offset,
        limit=limit,
        items=[
            AssetIngestionItemResponse(
                item_id=item.id,
                external_asset_id=item.external_asset_id,
                filename=item.filename,
                status=item.status,
                processing_job_id=item.processing_job_id,
                source_asset_id=item.source_asset_id,
                error_code=item.last_error_code,
                error_message=item.last_error_message,
                created_at=item.created_at,
                updated_at=item.updated_at,
                completed_at=item.completed_at,
            )
            for item in items
        ],
    )
