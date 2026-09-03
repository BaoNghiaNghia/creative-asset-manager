from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.modules.application_logs.model import LogApplicationModel
from app.modules.application_logs.repository import ApplicationLogRepository
from app.modules.application_logs.schema import LogApplicationCreateRequest, LogApplicationCreatedResponse, LogApplicationResponse, LogApplicationUpdateRequest
from app.modules.authorization.principal import CurrentPrincipal, require_permission, require_tenant_scope

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/log-applications", tags=["application-log-management"])


def _validate_schema(schema: dict | None) -> None:
    if schema is None: return
    try: Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_payload_schema", "message": exc.message}) from exc


def _response(row: LogApplicationModel, *, api_key: str | None = None):
    values = dict(id=row.id, tenant_id=row.tenant_id, slug=row.slug, display_name=row.display_name, payload_schema=row.payload_schema_json, key_prefix=row.key_prefix, active=row.active, created_at=row.created_at, updated_at=row.updated_at)
    return LogApplicationCreatedResponse(**values, api_key=api_key) if api_key is not None else LogApplicationResponse(**values)


@router.get("", response_model=list[LogApplicationResponse])
def list_applications(tenant_id: str, principal: CurrentPrincipal = Depends(require_permission("application_logs.manage"))):
    require_tenant_scope(principal, tenant_id)
    with SessionLocal() as session:
        rows = list(session.scalars(select(LogApplicationModel).where(LogApplicationModel.tenant_id == tenant_id).order_by(LogApplicationModel.display_name, LogApplicationModel.id)))
        return [_response(row) for row in rows]


@router.post("", response_model=LogApplicationCreatedResponse, status_code=201)
def create_application(tenant_id: str, body: LogApplicationCreateRequest, principal: CurrentPrincipal = Depends(require_permission("application_logs.manage"))):
    require_tenant_scope(principal, tenant_id); _validate_schema(body.payload_schema)
    with SessionLocal() as session:
        try:
            row, raw_key = ApplicationLogRepository(session).create_application(tenant_id=tenant_id, slug=body.slug, display_name=body.display_name, payload_schema=body.payload_schema)
            session.commit(); return _response(row, api_key=raw_key)
        except IntegrityError as exc:
            session.rollback(); raise HTTPException(status_code=409, detail="Application slug already exists") from exc


@router.patch("/{application_id}", response_model=LogApplicationResponse)
def update_application(tenant_id: str, application_id: str, body: LogApplicationUpdateRequest, principal: CurrentPrincipal = Depends(require_permission("application_logs.manage"))):
    require_tenant_scope(principal, tenant_id)
    if "payload_schema" in body.model_fields_set: _validate_schema(body.payload_schema)
    with SessionLocal() as session:
        row = session.scalar(select(LogApplicationModel).where(LogApplicationModel.tenant_id == tenant_id, LogApplicationModel.id == application_id))
        if row is None: raise HTTPException(status_code=404, detail="Log application not found")
        changes = body.model_fields_set
        if "display_name" in changes and body.display_name is None:
            raise HTTPException(status_code=422, detail="display_name cannot be null")
        if "active" in changes and body.active is None:
            raise HTTPException(status_code=422, detail="active cannot be null")
        if "display_name" in changes: row.display_name = body.display_name
        if "payload_schema" in changes: row.payload_schema_json = body.payload_schema
        if "active" in changes:
            row.active = body.active; row.revoked_at = None if body.active else datetime.now(timezone.utc)
        row.updated_at = datetime.now(timezone.utc); session.commit(); return _response(row)


@router.post("/{application_id}/rotate-key", response_model=LogApplicationCreatedResponse)
def rotate_application_key(tenant_id: str, application_id: str, principal: CurrentPrincipal = Depends(require_permission("application_logs.manage"))):
    require_tenant_scope(principal, tenant_id)
    with SessionLocal() as session:
        row = session.scalar(select(LogApplicationModel).where(LogApplicationModel.tenant_id == tenant_id, LogApplicationModel.id == application_id))
        if row is None: raise HTTPException(status_code=404, detail="Log application not found")
        raw_key = ApplicationLogRepository(session).rotate_key(row); row.active = True; row.revoked_at = None
        session.commit(); return _response(row, api_key=raw_key)
