from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.modules.auth_persistence.tenant_membership import TenantAccessError
from app.modules.authorization.admin_service import (
    TenantAccessAdminError,
    TenantAccessAdminService,
)
from app.modules.authorization.principal import (
    CurrentPrincipal,
    require_permission,
    require_tenant_scope,
)

from app.modules.authorization.service import AuthorizationError
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantMembershipModel

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}", tags=["access-management"])
MEMBERSHIP_STATUSES = {"invited", "active", "suspended", "removed"}
ROLE_STATUSES = {"active", "disabled"}


class MemberCreateRequest(BaseModel):
    user_id: str | None = Field(default=None, min_length=1, max_length=36)
    email: str | None = Field(default=None, min_length=3, max_length=512)
    status: Literal["invited", "active"] = "invited"
    reason: str = Field(min_length=3, max_length=1000)


class MembershipStatusRequest(BaseModel):
    action: Literal["activate", "suspend", "restore", "remove"]
    reason: str = Field(min_length=3, max_length=1000)
    allow_final_admin_override: bool = False


class RoleAssignmentRequest(BaseModel):
    role_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=3, max_length=1000)


class RoleRemovalRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    allow_final_admin_override: bool = False


class CustomRoleRequest(BaseModel):
    role_key: str = Field(min_length=2, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    permission_keys: set[str] = Field(default_factory=set, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)


class CustomRoleUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    permission_keys: set[str] = Field(default_factory=set, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)


class MutationReason(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ViewerFolderScopesRequest(BaseModel):
    external_source_id: str = Field(min_length=1, max_length=36)
    folders: list[dict[str, str]] = Field(default_factory=list, max_length=500)
    reason: str = Field(min_length=3, max_length=1000)


def _scope(principal: CurrentPrincipal, tenant_id: str) -> None:
    require_tenant_scope(principal, tenant_id)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, TenantAccessAdminError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, AuthorizationError):
        return HTTPException(
            status_code=403,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, TenantAccessError):
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, IntegrityError):
        return HTTPException(
            status_code=409,
            detail={"code": "conflict", "message": "The requested record already exists"},
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=422,
            detail={"code": "invalid_request", "message": str(exc)},
        )
    if isinstance(exc, LookupError):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "The requested record was not found"},
        )
    return HTTPException(
        status_code=500,
        detail={"code": "access_management_failed", "message": "Access management operation failed"},
    )


def _write(operation):
    with SessionLocal() as session:
        try:
            result = operation(TenantAccessAdminService(session))
            session.commit()
            return result
        except (TenantAccessAdminError, AuthorizationError, TenantAccessError, IntegrityError, ValueError, LookupError) as exc:
            session.rollback()
            raise _error(exc) from exc


@router.get("/members")
def list_members(
    tenant_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    query: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None),
    role: str | None = Query(default=None, max_length=128),
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.read")),
):
    _scope(principal, tenant_id)
    if status and status not in MEMBERSHIP_STATUSES:
        raise HTTPException(422, detail={"code": "invalid_membership_status", "message": "Unsupported membership status"})
    with SessionLocal() as session:
        return TenantAccessAdminService(session).list_members(
            tenant_id=tenant_id,
            page=page,
            page_size=page_size,
            query=query,
            status=status,
            role_key=role,
        )


@router.post("/members", status_code=201)
def add_member(
    tenant_id: str,
    body: MemberCreateRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.manage")),
):
    _scope(principal, tenant_id)
    membership = _write(
        lambda service: service.add_member(
            tenant_id=tenant_id,
            actor_user_id=principal.user_id,
            reason=body.reason,
            user_id=body.user_id,
            email=body.email,
            status=body.status,
        )
    )
    return {"membership_id": membership.id, "user_id": membership.user_id, "status": membership.status}


@router.patch("/members/{membership_id}")
def update_membership(
    tenant_id: str,
    membership_id: str,
    body: MembershipStatusRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.manage")),
):
    _scope(principal, tenant_id)
    membership = _write(
        lambda service: service.update_membership_status(
            tenant_id=tenant_id,
            membership_id=membership_id,
            action=body.action,
            actor_user_id=principal.user_id,
            reason=body.reason,
            platform_admin=principal.platform_admin,
            allow_final_admin_override=body.allow_final_admin_override,
        )
    )
    return {"membership_id": membership.id, "user_id": membership.user_id, "status": membership.status}


@router.post("/members/{membership_id}/roles", status_code=201)
def assign_role(
    tenant_id: str,
    membership_id: str,
    body: RoleAssignmentRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_roles.manage")),
):
    _scope(principal, tenant_id)
    assignment = _write(
        lambda service: service.assign_role(
            tenant_id=tenant_id,
            membership_id=membership_id,
            role_id=body.role_id,
            actor_user_id=principal.user_id,
            actor_permissions=principal.effective_permissions,
            platform_admin=principal.platform_admin,
            reason=body.reason,
        )
    )
    return {"assignment_id": assignment.id, "membership_id": membership_id, "role_id": body.role_id}


@router.delete("/members/{membership_id}/roles/{role_id}")
def remove_role(
    tenant_id: str,
    membership_id: str,
    role_id: str,
    body: RoleRemovalRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_roles.manage")),
):
    _scope(principal, tenant_id)
    removed = _write(
        lambda service: service.remove_role(
            tenant_id=tenant_id,
            membership_id=membership_id,
            role_id=role_id,
            actor_user_id=principal.user_id,
            reason=body.reason,
            platform_admin=principal.platform_admin,
            allow_final_admin_override=body.allow_final_admin_override,
        )
    )
    return {"removed": removed, "membership_id": membership_id, "role_id": role_id}


@router.get("/roles")
def list_roles(
    tenant_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    query: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None),
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.read")),
):
    _scope(principal, tenant_id)
    if status and status not in ROLE_STATUSES:
        raise HTTPException(422, detail={"code": "invalid_role_status", "message": "Unsupported role status"})
    with SessionLocal() as session:
        return TenantAccessAdminService(session).list_roles(
            tenant_id=tenant_id, page=page, page_size=page_size, query=query, status=status
        )


@router.get("/permissions")
def list_permissions(
    tenant_id: str,
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.read")),
):
    _scope(principal, tenant_id)
    with SessionLocal() as session:
        return {"items": TenantAccessAdminService(session).list_permissions()}


@router.post("/roles", status_code=201)
def create_role(
    tenant_id: str,
    body: CustomRoleRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_roles.manage")),
):
    _scope(principal, tenant_id)
    role = _write(
        lambda service: service.create_custom_role(
            tenant_id=tenant_id,
            actor_user_id=principal.user_id,
            actor_permissions=principal.effective_permissions,
            platform_admin=principal.platform_admin,
            role_key=body.role_key,
            name=body.name,
            description=body.description,
            permission_keys=body.permission_keys,
            reason=body.reason,
        )
    )
    return {"id": role.id, "key": role.role_key, "name": role.name, "protected": role.protected}


@router.patch("/roles/{role_id}")
def update_role(
    tenant_id: str,
    role_id: str,
    body: CustomRoleUpdateRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_roles.manage")),
):
    _scope(principal, tenant_id)
    role = _write(
        lambda service: service.update_custom_role(
            tenant_id=tenant_id,
            role_id=role_id,
            actor_user_id=principal.user_id,
            actor_permissions=principal.effective_permissions,
            platform_admin=principal.platform_admin,
            name=body.name,
            description=body.description,
            permission_keys=body.permission_keys,
            reason=body.reason,
        )
    )
    return {"id": role.id, "key": role.role_key, "name": role.name, "protected": role.protected}


@router.delete("/roles/{role_id}")
def delete_role(
    tenant_id: str,
    role_id: str,
    body: MutationReason,
    principal: CurrentPrincipal = Depends(require_permission("tenant_roles.manage")),
):
    _scope(principal, tenant_id)
    _write(
        lambda service: service.delete_custom_role(
            tenant_id=tenant_id,
            role_id=role_id,
            actor_user_id=principal.user_id,
            reason=body.reason,
        )
    )
    return {"deleted": True, "role_id": role_id}


@router.get("/members/{membership_id}/folder-scopes")
def list_viewer_folder_scopes(
    tenant_id: str, membership_id: str, external_source_id: str = Query(..., min_length=1),
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.manage")),
):
    _scope(principal, tenant_id)
    with SessionLocal() as session:
        membership = session.scalar(select(TenantMembershipModel).where(
            TenantMembershipModel.id == membership_id, TenantMembershipModel.tenant_id == tenant_id
        ))
        if membership is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "Membership was not found"})
        source = session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.id == external_source_id, ExternalSourceModel.tenant_id == tenant_id,
        ))
        if source is None:
            raise HTTPException(404, detail={"code": "not_found", "message": "Source was not found"})
        rows = ViewerFolderScopeService(session).list(
            tenant_id=tenant_id, membership_id=membership_id, external_source_id=external_source_id
        )
        return {"items": [{"id": row.id, "folder_id": row.folder_external_id, "folder_name": row.folder_name, "external_source_id": row.external_source_id} for row in rows]}


@router.put("/members/{membership_id}/folder-scopes")
def replace_viewer_folder_scopes(
    tenant_id: str, membership_id: str, body: ViewerFolderScopesRequest,
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.manage")),
):
    _scope(principal, tenant_id)
    def operation(service: TenantAccessAdminService):
        session = service.session
        membership = session.scalar(select(TenantMembershipModel).where(
            TenantMembershipModel.id == membership_id, TenantMembershipModel.tenant_id == tenant_id
        ))
        if membership is None:
            raise LookupError("membership")
        source = session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.id == body.external_source_id, ExternalSourceModel.tenant_id == tenant_id,
        ))
        if source is None:
            raise LookupError("source")
        result = ViewerFolderScopeService(session).replace(
            tenant_id=tenant_id, membership_id=membership_id,
            external_source_id=body.external_source_id, folders=body.folders,
        )
        service._audit("viewer_folder_scopes_replaced", tenant_id, principal.user_id, body.reason, {
            "membership_id": membership_id, "external_source_id": body.external_source_id,
            "folder_count": len(result),
        })
        return {"items": [{"id": row.id, "folder_id": row.folder_external_id, "folder_name": row.folder_name, "external_source_id": row.external_source_id} for row in result]}
    return _write(operation)
