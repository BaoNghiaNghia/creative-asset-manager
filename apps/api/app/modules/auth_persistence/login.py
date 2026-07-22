from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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
        first_login = existing is None
        if first_login:
            self._require_signup_admission(provider_email)

        user, identity = self.identities.resolve_login(
            provider=provider,
            provider_subject=provider_subject,
            provider_email=provider_email,
            display_name=display_name,
            avatar_url=avatar_url,
            provider_tenant_id=provider_tenant_id,
            provider_metadata=provider_metadata,
        )
        if first_login:
            self._provision_first_membership(user, display_name)

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
        self, user: UserModel, display_name: str | None
    ) -> None:
        default_tenant_id = self.settings.AUTH_DEFAULT_TENANT_ID.strip()
        if default_tenant_id:
            tenant = self.session.get(TenantModel, default_tenant_id)
            if tenant is None or tenant.status != "active":
                raise LoginAdmissionError(
                    "default_tenant_unavailable",
                    "The configured default tenant is unavailable",
                )
            self.memberships.add_member(
                tenant_id=tenant.id, user_id=user.id, status="active"
            )
            return
        personal = self.memberships.ensure_development_personal_tenant(
            settings=self.settings,
            user=user,
            legacy_tenant_id=user.id,
            display_name=display_name,
        )
        if personal is None:
            raise LoginAdmissionError(
                "tenant_membership_required",
                "A default tenant or pre-provisioned membership is required",
            )
