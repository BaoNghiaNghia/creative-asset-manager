from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    TenantMembershipModel,
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
            provider,
            provider_subject,
        )

        provisioned_user = None
        activate_invitation = False
        accept_existing_invitation = False
        admission = "existing_identity"

        if existing is not None:
            existing_user = self.session.get(
                UserModel,
                existing.user_id,
            )

            accept_existing_invitation = (
                self._has_matching_invitation(
                    user=existing_user,
                    provider=provider,
                    provider_email=provider_email,
                    provider_metadata=provider_metadata,
                )
            )
        else:
            provisioned_user = self._find_unlinked_provisioned_user(
                provider=provider,
                provider_email=provider_email,
                provider_metadata=provider_metadata,
            )

            if provisioned_user is None:
                self._require_signup_admission(provider_email)
                admission = "self_signup"
            else:
                activate_invitation = self._has_invited_membership(provisioned_user)
                admission = (
                    "invitation"
                    if activate_invitation
                    else "preprovisioned_member"
                )

        # Identity creation/linking, invitation activation and role assignment
        # are performed in one database savepoint.
        with self.session.begin_nested():
            if existing is None and provisioned_user is not None:
                user = provisioned_user

                identity = self.identities.link_identity_to_user(
                    user_id=user.id,
                    provider=provider,
                    provider_subject=provider_subject,
                    provider_email=provider_email,
                    provider_tenant_id=provider_tenant_id,
                    provider_metadata=provider_metadata,
                )

                self.identities.update_safe_profile_fields(
                    user,
                    identity,
                    provider_email=provider_email,
                    display_name=display_name,
                    avatar_url=avatar_url,
                    provider_tenant_id=provider_tenant_id,
                    provider_metadata=provider_metadata,
                )

                self.identities.record_login(user, identity)
                first_login = True

            else:
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

            if (
                activate_invitation
                or accept_existing_invitation
            ):
                self._activate_invited_memberships(
                    user=user,
                    identity=identity,
                )

        available = self.memberships.list_user_tenants(user.id)

        if not available:
            raise LoginAdmissionError(
                "tenant_membership_required",
                "An active tenant membership is required",
            )

        default_tenant_id = (
            self.settings.AUTH_DEFAULT_TENANT_ID.strip()
        )

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
                        "admission": admission,
                    },
                )
            )

        self.session.flush()

        return ApplicationLogin(
            user,
            identity,
            selected.id,
            first_login,
        )

    @staticmethod
    def _verified_google_email(
        *,
        provider: str,
        provider_email: str | None,
        provider_metadata: Mapping[str, Any] | None,
    ) -> str | None:
        if provider.strip().casefold() != "google":
            return None

        metadata = provider_metadata or {}

        if metadata.get("email_verified") is not True:
            return None

        return normalize_email(provider_email)

    def _find_unlinked_provisioned_user(
        self,
        *,
        provider: str,
        provider_email: str | None,
        provider_metadata: Mapping[str, Any] | None,
    ) -> UserModel | None:
        verified_email = self._verified_google_email(
            provider=provider,
            provider_email=provider_email,
            provider_metadata=provider_metadata,
        )

        if not verified_email:
            return None

        provisioned_user_ids = (
            select(TenantMembershipModel.user_id)
            .where(TenantMembershipModel.status.in_(("invited", "active")))
        )

        identity_exists = (
            select(UserIdentityModel.id)
            .where(UserIdentityModel.user_id == UserModel.id)
            .exists()
        )

        candidates = list(
            self.session.scalars(
                select(UserModel)
                .where(
                    UserModel.id.in_(provisioned_user_ids),
                    UserModel.primary_email == verified_email,
                    UserModel.status == "active",
                    ~identity_exists,
                )
                .with_for_update()
                .limit(2)
            )
        )

        if len(candidates) > 1:
            raise LoginAdmissionError(
                "invitation_ambiguous",
                (
                    "More than one pending invitation matches "
                    "this verified email"
                ),
            )

        return candidates[0] if candidates else None

    def _has_invited_membership(self, user: UserModel) -> bool:
        return self.session.scalar(
            select(TenantMembershipModel.id)
            .where(
                TenantMembershipModel.user_id == user.id,
                TenantMembershipModel.status == "invited",
            )
            .limit(1)
        ) is not None

    def _has_matching_invitation(
        self,
        *,
        user: UserModel | None,
        provider: str,
        provider_email: str | None,
        provider_metadata: Mapping[str, Any] | None,
    ) -> bool:
        if user is None or user.status != "active":
            return False

        verified_email = self._verified_google_email(
            provider=provider,
            provider_email=provider_email,
            provider_metadata=provider_metadata,
        )

        if (
            not verified_email
            or normalize_email(user.primary_email)
            != verified_email
        ):
            return False

        membership_id = self.session.scalar(
            select(TenantMembershipModel.id)
            .where(
                TenantMembershipModel.user_id == user.id,
                TenantMembershipModel.status == "invited",
            )
            .limit(1)
        )

        return membership_id is not None

    def _activate_invited_memberships(
        self,
        *,
        user: UserModel,
        identity: UserIdentityModel,
    ) -> None:
        memberships = list(
            self.session.scalars(
                select(TenantMembershipModel)
                .where(
                    TenantMembershipModel.user_id == user.id,
                    TenantMembershipModel.status == "invited",
                )
                .with_for_update()
            )
        )

        if not memberships:
            raise LoginAdmissionError(
                "invitation_unavailable",
                "No usable pending invitation remains",
            )

        role_key = (
            self.settings.AUTH_SELF_SIGNUP_DEFAULT_ROLE
            .strip()
            .casefold()
        )

        if not role_key:
            raise LoginAdmissionError(
                "default_role_unavailable",
                "The default invited-user role is not configured",
            )

        if role_key in {"tenant_admin", "platform_admin"}:
            raise LoginAdmissionError(
                "default_role_forbidden",
                "An invitation cannot grant an administrator role",
            )

        now = datetime.now(timezone.utc)
        activated = 0

        for membership in memberships:
            tenant = self.session.get(
                TenantModel,
                membership.tenant_id,
            )

            if tenant is None or tenant.status != "active":
                continue

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
                    (
                        "The configured invited-user role "
                        "is unavailable"
                    ),
                )

            membership.status = "active"
            membership.joined_at = membership.joined_at or now
            membership.updated_at = now

            # assign_role requires the membership to be active.
            self.session.flush()

            self.authorization.assign_role(
                tenant_id=tenant.id,
                membership_id=membership.id,
                role_id=role.id,
                actor_id=(
                    membership.invited_by_user_id
                    or user.id
                ),
                reason=(
                    "Verified Google email accepted "
                    "the pending invitation"
                ),
            )

            self.session.add(
                AuthAuditEventModel(
                    tenant_id=tenant.id,
                    actor_id=user.id,
                    provider=identity.provider,
                    action="tenant_invitation_accepted",
                    detail_json={
                        "membership_id": membership.id,
                        "identity_id": identity.id,
                        "role_key": role.role_key,
                        "verified_email_match": True,
                    },
                )
            )

            activated += 1

        if activated == 0:
            raise LoginAdmissionError(
                "invitation_tenant_unavailable",
                "The invited tenant is unavailable",
            )
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
