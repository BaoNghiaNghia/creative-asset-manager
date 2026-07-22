from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.auth_persistence.tenant_membership import TenantAccessError, TenantMembershipService
from app.providers.google.auth import get_session as get_google_session
from app.providers.microsoft.auth import get_session as get_microsoft_session


@dataclass(frozen=True, slots=True)
class ProcessingAdmin:
    actor_id: str
    own_tenant_id: str
    platform_admin: bool

    def authorize_tenant(self, tenant_id: str) -> None:
        if not self.platform_admin and tenant_id != self.own_tenant_id:
            raise HTTPException(status_code=403, detail="Tenant policy access denied")


def resolve_processing_tenant(session) -> str:
    """AUTH-02 compatibility resolver for legacy and membership sessions."""
    with SessionLocal() as database:
        try:
            context = TenantMembershipService(database).resolve_active_tenant(session)
            database.commit()
            return context.tenant_id
        except TenantAccessError as exc:
            database.rollback()
            raise HTTPException(status_code=403, detail=exc.code) from exc


def require_processing_admin(request: Request) -> ProcessingAdmin:
    session = get_google_session(request) or get_microsoft_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    legacy_actor = str(session.user.get("id") or session.user.get("email") or "")
    actor = str(session.user_id or legacy_actor)
    if not actor:
        raise HTTPException(status_code=403, detail="Authenticated account has no tenant identity")
    own_tenant_id = resolve_processing_tenant(session)
    roles = session.user.get("roles") or []
    role = str(session.user.get("role") or "").lower()
    tenant_admin = bool(session.user.get("is_admin")) or role in {"admin", "tenant_admin", "platform_admin"} or "admin" in roles
    configured = {value.strip() for value in get_settings().PROCESSING_POLICY_ADMIN_IDS.split(",") if value.strip()}
    platform_admin = actor in configured or legacy_actor in configured or role == "platform_admin"
    if not tenant_admin and not platform_admin:
        raise HTTPException(status_code=403, detail="Processing policy administrator role required")
    return ProcessingAdmin(actor, own_tenant_id, platform_admin)
