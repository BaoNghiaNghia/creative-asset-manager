from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from sqlalchemy.exc import IntegrityError

from app.modules.application_logs.auth import LogApplicationContext, authenticate_log_application
from app.modules.application_logs.repository import IdempotencyConflictError
from app.modules.application_logs.schema import MAX_LOG_PAYLOAD_BYTES, ApplicationLogCreateRequest, ApplicationLogListResponse, ApplicationLogResponse

router = APIRouter(prefix="/api/v1/application-logs", tags=["application-logs"])


def _response(context: LogApplicationContext, row) -> ApplicationLogResponse:
    return ApplicationLogResponse(
        id=row.id, application_id=row.application_id, application_slug=context.application.slug,
        level=row.level, event_type=row.event_type, message=row.message, trace_id=row.trace_id,
        payload=row.payload_json, occurred_at=row.occurred_at,
        received_at=row.received_at, expires_at=row.expires_at,
    )


def _validate_payload(context: LogApplicationContext, payload: dict) -> None:
    schema = context.application.payload_schema_json
    if schema is None: return
    try:
        Draft202012Validator(schema).validate(payload)
    except JsonSchemaValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path) or "$"
        raise HTTPException(status_code=422, detail={"code": "payload_schema_mismatch", "message": exc.message, "path": path}) from exc


@router.post("", response_model=ApplicationLogResponse, status_code=201)
def create_log(
    body: ApplicationLogCreateRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: LogApplicationContext = Depends(authenticate_log_application),
):
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be 1 to 255 characters")
    payload_bytes = len(json.dumps(body.payload, separators=(",", ":")).encode("utf-8"))
    if payload_bytes > MAX_LOG_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Log payload is too large")
    _validate_payload(context, body.payload)
    now = datetime.now(timezone.utc)
    request_hash = hashlib.sha256(body.model_dump_json(exclude_none=False).encode("utf-8")).hexdigest()
    try:
        context.repository.purge_expired(now=now, tenant_id=context.application.tenant_id)
        row, created = context.repository.create_log(
            application=context.application, idempotency_key=idempotency_key, request_hash=request_hash,
            level=body.level, event_type=body.event_type, message=body.message,
            trace_id=body.trace_id, payload=body.payload, occurred_at=body.occurred_at, now=now,
        )
        context.repository.session.commit()
    except IdempotencyConflictError as exc:
        context.repository.session.rollback()
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different log") from exc
    except IntegrityError as exc:
        context.repository.session.rollback()
        raise HTTPException(status_code=409, detail="Log idempotency conflict") from exc
    if not created: response.status_code = 200
    return _response(context, row)


@router.get("", response_model=ApplicationLogListResponse)
def list_logs(
    start: datetime | None = Query(default=None, alias="from"),
    end: datetime | None = Query(default=None, alias="to"),
    level: Literal["trace", "debug", "info", "warning", "error", "critical"] | None = None,
    event_type: str | None = Query(default=None, max_length=128),
    trace_id: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
    context: LogApplicationContext = Depends(authenticate_log_application),
):
    if start and end and start > end: raise HTTPException(status_code=422, detail="from must be before or equal to to")
    now = datetime.now(timezone.utc)
    context.repository.purge_expired(now=now, tenant_id=context.application.tenant_id)
    rows, total = context.repository.list_logs(
        application_id=context.application.id, now=now, start=start, end=end,
        level=level, event_type=event_type, trace_id=trace_id, limit=limit, offset=offset,
    )
    context.repository.session.commit()
    return ApplicationLogListResponse(items=[_response(context, row) for row in rows], total=total, offset=offset, limit=limit)
