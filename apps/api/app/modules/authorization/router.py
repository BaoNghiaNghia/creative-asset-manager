from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.auth_persistence.identity import ApplicationUserInactiveError
from app.modules.auth_persistence.service import auth_repository, cookie_options
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.auth_persistence.model import UserModel
from app.modules.authorization.principal import (
    CurrentPrincipal,
    authorization_error,
    require_authenticated_principal,
)
from app.modules.authorization.service import TenantAuthorizationService
from app.providers.google.auth import SESSION_COOKIE as GOOGLE_SESSION_COOKIE
from app.providers.microsoft.auth import SESSION_COOKIE as MICROSOFT_SESSION_COOKIE


class ActiveTenantRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=255)

router = APIRouter(prefix="/api/v1/auth", tags=["authorization"])


@router.get("/identity")
def identity(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
):
    with SessionLocal() as database:
        available = TenantMembershipService(database).list_user_tenants(
            principal.user_id
        )
        user = database.get(UserModel, principal.user_id)
        tenants = [
            {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
            }
            for _membership, tenant in available
        ]
    return {
        "user_id": principal.user_id,
        "actor_id": principal.actor_id,
        "active_tenant_id": principal.active_tenant_id,
        "available_tenants": tenants,
        "roles": sorted(principal.effective_roles),
        "permissions": sorted(principal.effective_permissions),
        "is_processing_admin": principal.platform_admin
        or "tenant_admin" in principal.effective_roles,
        "authorization_source": principal.authorization_source,
        "display_name": user.display_name if user else None,
        "email": user.primary_email if user else None,
        "avatar_url": user.avatar_url if user else None,
    }


@router.post("/active-tenant")
def select_active_tenant(
    body: ActiveTenantRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
):
    provider = principal.external_identity.provider if principal.external_identity else ""
    cookie_name = {
        "google": GOOGLE_SESSION_COOKIE,
        "microsoft": MICROSOFT_SESSION_COOKIE,
    }.get(provider)
    raw_session_id = request.cookies.get(cookie_name) if cookie_name else None
    if not raw_session_id:
        raise authorization_error(
            401, "authentication_required", "Application session is required"
        )
    with SessionLocal() as database:
        available = TenantMembershipService(database).list_user_tenants(
            principal.user_id
        )
        effective = TenantAuthorizationService(database).get_effective_permissions(
            tenant_id=body.tenant_id,
            user_id=principal.user_id,
        )
        user = database.get(UserModel, principal.user_id)
        tenants = [
            {"id": tenant.id, "name": tenant.name, "slug": tenant.slug}
            for _membership, tenant in available
        ]

    try:
        with auth_repository() as repository:
            replacement_id, _replacement = repository.rotate_session_active_tenant(
                provider=provider,
                session_id=raw_session_id,
                user_id=principal.user_id,
                tenant_id=body.tenant_id,
                ttl_seconds=get_settings().AUTH_SESSION_TTL_SECONDS,
            )
    except ApplicationUserInactiveError as exc:
        raise authorization_error(
            403, "user_disabled", "Application user is disabled"
        ) from exc
    except PermissionError as exc:
        raise authorization_error(
            403,
            "tenant_membership_required",
            "An active tenant membership is required",
        ) from exc
    except LookupError as exc:
        raise authorization_error(
            401, "authentication_required", "Application session is invalid"
        ) from exc

    payload = {
        "user_id": principal.user_id,
        "actor_id": principal.actor_id,
        "active_tenant_id": body.tenant_id,
        "available_tenants": tenants,
        "roles": sorted(effective.roles),
        "permissions": sorted(effective.permissions),
        "is_processing_admin": principal.platform_admin or "tenant_admin" in effective.roles,
        "authorization_source": principal.authorization_source,
        "display_name": user.display_name if user else None,
        "email": user.primary_email if user else None,
        "avatar_url": user.avatar_url if user else None,
    }
    response = JSONResponse(payload)
    response.set_cookie(cookie_name, replacement_id, **cookie_options())
    return response
