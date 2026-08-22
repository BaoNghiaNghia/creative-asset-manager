from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.auth_persistence.model import (
    AuthSessionModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.auth_persistence.repository import digest
from app.modules.auth_persistence.tenant_membership import (
    TenantAccessError,
    TenantMembershipService,
)
from app.modules.authorization.platform_admin import PlatformAdminService
from app.modules.authorization.principal_cache import principal_cache
from app.modules.authorization.service import TenantAuthorizationService
from app.providers.google.auth import (
    SESSION_COOKIE as GOOGLE_SESSION_COOKIE,
    get_session as get_google_session,
)
from app.providers.microsoft.auth import (
    SESSION_COOKIE as MICROSOFT_SESSION_COOKIE,
    get_session as get_microsoft_session,
)


@dataclass(frozen=True, slots=True)
class ExternalIdentitySummary:
    provider: str
    provider_subject: str
    provider_email: str | None


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    user_id: str
    active_tenant_id: str
    membership_id: str
    external_identity: ExternalIdentitySummary | None
    effective_roles: frozenset[str]
    effective_permissions: frozenset[str]
    platform_admin: bool
    session_id: str
    authorization_source: str

    @property
    def actor_id(self) -> str:
        if self.external_identity is not None:
            return self.external_identity.provider_subject
        return self.user_id


def is_pure_viewer(principal: CurrentPrincipal | object) -> bool:
    privileged = {"operator", "tenant_admin", "billing_admin"}
    roles = frozenset(getattr(principal, "effective_roles", frozenset()))
    return (
        not bool(getattr(principal, "platform_admin", False))
        and "viewer" in roles
        and not roles.intersection(privileged)
    )


def authorization_error(
    status_code: int,
    code: str,
    message: str,
    *,
    required_permission: str | None = None,
) -> HTTPException:
    detail: dict[str, str] = {"code": code, "message": message}
    if required_permission:
        detail["required_permission"] = required_permission
    return HTTPException(status_code=status_code, detail=detail)


def _configured_legacy_platform_admin(cloud_session, identity) -> bool:
    settings = get_settings()
    if not settings.AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED:
        return False
    configured = {
        value.strip()
        for value in settings.PROCESSING_POLICY_ADMIN_IDS.split(",")
        if value.strip()
    }
    candidates = {
        str(cloud_session.user_id or ""),
        str(cloud_session.user.get("id") or ""),
        str(cloud_session.user.get("email") or ""),
    }
    if identity is not None:
        candidates.add(identity.provider_subject)
    return bool(configured.intersection(candidates))


def _active_session_user_from_cookie(request: Request) -> UserModel | None:
    """Distinguish an inactive user from a missing/expired session safely."""
    cookies = (
        ("google", request.cookies.get(GOOGLE_SESSION_COOKIE)),
        ("microsoft", request.cookies.get(MICROSOFT_SESSION_COOKIE)),
    )
    now = datetime.now(timezone.utc)
    with SessionLocal() as database:
        for provider, session_id in cookies:
            if not session_id:
                continue
            row = database.scalar(
                select(AuthSessionModel).where(
                    AuthSessionModel.session_id_hash == digest(session_id),
                    AuthSessionModel.provider == provider,
                    AuthSessionModel.expires_at > now,
                )
            )
            if row is not None and row.user_id is not None:
                return database.get(UserModel, row.user_id)
    return None


def require_authenticated_principal(request: Request) -> CurrentPrincipal:
    cloud_session = get_google_session(request) or get_microsoft_session(request)
    if cloud_session is None:
        application_user = _active_session_user_from_cookie(request)
        if application_user is not None and application_user.status != "active":
            raise authorization_error(403, "user_disabled", "Application user is disabled")
        raise authorization_error(401, "authentication_required", "Authentication is required")
    if cloud_session.user_id is None:
        raise authorization_error(401, "authentication_required", "Application session is required")

    cached = principal_cache.get(
        cloud_session.session_id_hash, cloud_session.active_tenant_id
    )
    if cached is not None:
        return cached

    with SessionLocal() as database:
        application_user = database.get(UserModel, cloud_session.user_id)
        if application_user is None:
            raise authorization_error(401, "authentication_required", "Application session is invalid")
        if application_user.status != "active":
            raise authorization_error(403, "user_disabled", "Application user is disabled")

        memberships = TenantMembershipService(database)
        try:
            tenant_context = memberships.resolve_active_tenant(cloud_session)
        except TenantAccessError as exc:
            database.rollback()
            if exc.code == "inactive_user":
                raise authorization_error(403, "user_disabled", "Application user is disabled") from exc
            raise authorization_error(
                403,
                "tenant_membership_required",
                "An active tenant membership is required",
            ) from exc

        effective = TenantAuthorizationService(database).get_effective_permissions(
            tenant_id=tenant_context.tenant_id,
            user_id=application_user.id,
        )
        identity = database.scalar(
            select(UserIdentityModel)
            .where(
                UserIdentityModel.user_id == application_user.id,
                UserIdentityModel.provider == cloud_session.provider,
            )
            .order_by(UserIdentityModel.last_login_at.desc(), UserIdentityModel.id)
        )
        durable_platform_admin = PlatformAdminService(database).is_platform_admin(
            application_user.id
        )
        compatibility_admin = _configured_legacy_platform_admin(
            cloud_session, identity
        )
        database.commit()

    external_identity = None
    if identity is not None:
        external_identity = ExternalIdentitySummary(
            provider=identity.provider,
            provider_subject=identity.provider_subject,
            provider_email=identity.provider_email,
        )
    authorization_source = "tenant_rbac"
    if durable_platform_admin:
        authorization_source = "durable_platform_admin"
    elif compatibility_admin:
        authorization_source = "deprecated_processing_admin_allowlist"
    principal = CurrentPrincipal(
        user_id=application_user.id,
        active_tenant_id=tenant_context.tenant_id,
        membership_id=tenant_context.membership_id or "",
        external_identity=external_identity,
        effective_roles=effective.roles,
        effective_permissions=effective.permissions,
        platform_admin=durable_platform_admin or compatibility_admin,
        session_id=cloud_session.session_id_hash,
        authorization_source=authorization_source,
    )
    principal_cache.put(principal)
    return principal


def require_permission(permission_key: str):
    return require_all_permissions(permission_key)


def require_any_permission(*permission_keys: str):
    required = frozenset(value for value in permission_keys if value)
    if not required:
        raise ValueError("at least one permission is required")

    def dependency(
        principal: CurrentPrincipal = Depends(require_authenticated_principal),
    ) -> CurrentPrincipal:
        if principal.platform_admin or required.intersection(
            principal.effective_permissions
        ):
            return principal
        raise authorization_error(
            403,
            "permission_required",
            "One of the required permissions is missing",
            required_permission="|".join(sorted(required)),
        )

    return dependency


def require_all_permissions(*permission_keys: str):
    required = frozenset(value for value in permission_keys if value)
    if not required:
        raise ValueError("at least one permission is required")

    def dependency(
        principal: CurrentPrincipal = Depends(require_authenticated_principal),
    ) -> CurrentPrincipal:
        if principal.platform_admin or required.issubset(
            principal.effective_permissions
        ):
            return principal
        raise authorization_error(
            403,
            "permission_required",
            "Required permission is missing",
            required_permission=",".join(sorted(required)),
        )

    return dependency

def require_principal_permission(
    principal: CurrentPrincipal, permission_key: str
) -> None:
    """Authorize a permission selected by domain input, not route wiring."""
    if principal.platform_admin or permission_key in principal.effective_permissions:
        return
    raise authorization_error(
        403,
        "permission_required",
        "Required permission is missing",
        required_permission=permission_key,
    )


def require_platform_admin(
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
) -> CurrentPrincipal:
    if principal.platform_admin:
        return principal
    raise authorization_error(
        403,
        "permission_required",
        "Platform administrator permission is required",
        required_permission="platform_admin",
    )


def require_tenant_scope(principal: CurrentPrincipal, tenant_id: str) -> None:
    if not principal.platform_admin and tenant_id != principal.active_tenant_id:
        raise authorization_error(403, "tenant_mismatch", "Tenant access is denied")
