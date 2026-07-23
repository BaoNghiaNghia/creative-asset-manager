from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth_persistence.identity import (
    IdentityResolutionService,
    normalize_email,
)
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    TenantModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.model import RoleModel
from app.modules.authorization.seed import seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService


class LoginAdmissionError(PermissionError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApplicationLogin:
    user: UserModel
    identity: UserIdentityModel
    active_tenant_id: str
    first_login: bool


class ApplicationLoginService:
    """Resolve external identity, admission and active tenant atomically."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.identities = IdentityResolutionService(session)
        self.memberships = TenantMembershipService(session)
        self.authorization = TenantAuthorizationService(session)

    def resolve(
        self,
        *,
        provider: str,
        provider_subject: str,
        provider_email: str | None,
        display_name: str | None,
        avatar_url: str | None = None,
        provider_tenant_id: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ApplicationLogin:
        existing = self.identities.find_by_provider_subject(
            provider, provider_subject
        )
        if existing is None:
            self._require_signup_admission(provider_email)

        # Keep JIT provisioning all-or-nothing inside the caller's larger
        # OAuth connection/session transaction.
        with self.session.begin_nested():
            user, identity, first_login = (
                self.identities.resolve_login_with_status(
                    provider=provider,
                    provider_subject=provider_subject,
                    provider_email=provider_email,
                    display_name=display_name,
                    avatar_url=avatar_url,
                    provider_tenant_id=provider_tenant_id,
                    provider_metadata=provider_metadata,
                )
            )
            if first_login:
                self._provision_first_membership(
                    user=user,
                    identity=identity,
                    display_name=display_name,
                )

            available = self.memberships.list_user_tenants(user.id)
            if not available:
                raise LoginAdmissionError(
                    "tenant_membership_required",
                    "An active tenant membership is required",
                )
            default_tenant_id = self.settings.AUTH_DEFAULT_TENANT_ID.strip()
            selected = next(
                (
                    tenant
                    for _membership, tenant in available
                    if tenant.id == default_tenant_id
                ),
                available[0][1],
            )
            self.session.add(
                AuthAuditEventModel(
                    tenant_id=selected.id,
                    actor_id=user.id,
                    provider=identity.provider,
                    action="application_login",
                    detail_json={
                        "identity_id": identity.id,
                        "first_login": first_login,
                        "active_tenant_id": selected.id,
                    },
                )
            )
            if first_login:
                self.session.add(
                    AuthAuditEventModel(
                        tenant_id=selected.id,
                        actor_id=user.id,
                        provider=identity.provider,
                        action="application_user_registered",
                        detail_json={
                            "identity_id": identity.id,
                            "admission": "self_signup",
                        },
                    )
                )
            self.session.flush()
        return ApplicationLogin(user, identity, selected.id, first_login)

    def _require_signup_admission(self, provider_email: str | None) -> None:
        if not self.settings.AUTH_SELF_SIGNUP_ENABLED:
            raise LoginAdmissionError(
                "self_signup_disabled", "Application self-signup is disabled"
            )
        allowed_domains = set(self.settings.auth_allowed_email_domains)
        if not allowed_domains:
            return
        email = normalize_email(provider_email)
        domain = email.rsplit("@", 1)[1] if email and "@" in email else ""
        if domain not in allowed_domains:
            raise LoginAdmissionError(
                "email_domain_not_allowed", "Email domain is not allowed"
            )

    def _provision_first_membership(
        self,
        *,
        user: UserModel,
        identity: UserIdentityModel,
        display_name: str | None,
    ) -> None:
        default_tenant_id = self.settings.AUTH_DEFAULT_TENANT_ID.strip()
        if default_tenant_id:
            tenant = self.session.get(TenantModel, default_tenant_id)
            if tenant is None or tenant.status != "active":
                raise LoginAdmissionError(
                    "default_tenant_unavailable",
                    "The configured default tenant is unavailable",
                )
            membership = self.memberships.add_member(
                tenant_id=tenant.id, user_id=user.id, status="active"
            )
        else:
            tenant = self.memberships.ensure_development_personal_tenant(
                settings=self.settings,
                user=user,
                legacy_tenant_id=user.id,
                display_name=display_name,
            )
            if tenant is None:
                raise LoginAdmissionError(
                    "tenant_membership_required",
                    "A default tenant or pre-provisioned membership is required",
                )
            membership = self.memberships.get_membership(tenant.id, user.id)
            if membership is None:
                raise RuntimeError("JIT membership provisioning failed")
            # Personal tenants exist only behind the explicit development flag.
            seed_tenant_rbac(self.session, tenant.id)

        role_key = self.settings.AUTH_SELF_SIGNUP_DEFAULT_ROLE
        role = self.session.scalar(
            select(RoleModel).where(
                RoleModel.tenant_id == tenant.id,
                RoleModel.role_key == role_key,
                RoleModel.status == "active",
            )
        )
        if role is None:
            raise LoginAdmissionError(
                "default_role_unavailable",
                "The configured self-signup role is unavailable",
            )
        if role.role_key in {"tenant_admin", "platform_admin"}:
            raise LoginAdmissionError(
                "default_role_forbidden",
                "The configured self-signup role cannot grant administration",
            )
        self.authorization.assign_role(
            tenant_id=tenant.id,
            membership_id=membership.id,
            role_id=role.id,
            actor_id=user.id,
            reason="secure JIT self-signup default role",
        )
        for action, detail in (
            ("application_user_created", {"user_id": user.id}),
            ("provider_identity_created", {"identity_id": identity.id}),
            (
                "tenant_membership_created",
                {"membership_id": membership.id},
            ),
            (
                "self_signup_default_role_assigned",
                {
                    "membership_id": membership.id,
                    "role_id": role.id,
                    "role_key": role.role_key,
                },
            ),
        ):
            self.session.add(
                AuthAuditEventModel(
                    tenant_id=tenant.id,
                    actor_id=user.id,
                    provider=identity.provider,
                    action=action,
                    detail_json=detail,
                )
            )
