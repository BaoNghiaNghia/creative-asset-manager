from fastapi import APIRouter, Depends

from app.core.database import SessionLocal
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.principal import (
    CurrentPrincipal,
    require_authenticated_principal,
)

router = APIRouter(prefix="/api/v1/auth", tags=["authorization"])


@router.get("/identity")
def identity(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
):
    with SessionLocal() as database:
        available = TenantMembershipService(database).list_user_tenants(
            principal.user_id
        )
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
    }
