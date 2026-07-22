from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth_persistence.identity import IdentityResolutionService
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    AuthSessionModel,
    OAuthConnectionModel,
    TenantModel,
    UserModel,
)
from app.modules.auth_persistence.tenant_membership import (
    TenantMembershipService,
    normalize_slug,
)
from app.modules.authorization.model import RoleModel
from app.modules.authorization.platform_admin import PlatformAdminService
from app.modules.authorization.seed import seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService


def _reason(value: str) -> str:
    normalized = value.strip()[:1000]
    if not normalized:
        raise ValueError("an audit reason is required")
    return normalized


def _active_identity(session: Session, provider: str, subject: str):
    identity = IdentityResolutionService(session).find_by_provider_subject(
        provider, subject
    )
    if identity is None:
        raise LookupError("application identity not found")
    user = session.get(UserModel, identity.user_id)
    if user is None or user.status != "active":
        raise LookupError("active application user not found")
    return user, identity


def bootstrap_identity_access(
    session: Session,
    *,
    provider: str,
    subject: str,
    tenant_id: str | None,
    tenant_name: str | None,
    tenant_slug: str | None,
    reason: str,
) -> dict:
    """Idempotently create/select a tenant, membership and tenant_admin role."""
    audit_reason = _reason(reason)
    user, identity = _active_identity(session, provider, subject)
    memberships = TenantMembershipService(session)
    tenant = session.get(TenantModel, tenant_id) if tenant_id else None
    normalized_slug = normalize_slug(tenant_slug) if tenant_slug else None
    if tenant is None and normalized_slug:
        tenant = session.scalar(
            select(TenantModel).where(TenantModel.slug == normalized_slug)
        )
    tenant_created = tenant is None
    if tenant is None:
        if not tenant_name or not normalized_slug:
            raise ValueError(
                "tenant_name and tenant_slug are required when creating a tenant"
            )
        tenant = memberships.create_tenant(
            tenant_id=tenant_id, name=tenant_name, slug=normalized_slug
        )
    elif tenant.status != "active":
        raise ValueError("selected tenant is not active")
    elif normalized_slug and tenant.slug != normalized_slug:
        raise ValueError("selected tenant does not match requested slug")

    membership = memberships.get_membership(tenant.id, user.id)
    membership_created = membership is None
    if membership is None:
        membership = memberships.add_member(
            tenant_id=tenant.id, user_id=user.id, status="active"
        )
    elif membership.status != "active":
        membership = memberships.restore_member(tenant.id, user.id)

    seed_result = seed_tenant_rbac(session, tenant.id)
    role = session.scalar(
        select(RoleModel).where(
            RoleModel.tenant_id == tenant.id,
            RoleModel.role_key == "tenant_admin",
            RoleModel.status == "active",
        )
    )
    if role is None:
        raise RuntimeError("tenant_admin role was not seeded")
    assignment = TenantAuthorizationService(session).assign_role(
        tenant_id=tenant.id,
        membership_id=membership.id,
        role_id=role.id,
        actor_id=user.id,
        reason=audit_reason,
    )
    session.add(
        AuthAuditEventModel(
            tenant_id=tenant.id,
            actor_id=user.id,
            provider=identity.provider,
            action="auth_access_bootstrapped",
            detail_json={
                "reason": audit_reason,
                "tenant_created": tenant_created,
                "membership_created": membership_created,
                "membership_id": membership.id,
                "role_id": role.id,
            },
        )
    )
    session.flush()
    return {
        "user_id": user.id,
        "tenant_id": tenant.id,
        "membership_id": membership.id,
        "role_assignment_id": assignment.id,
        "tenant_created": tenant_created,
        "membership_created": membership_created,
        **seed_result,
    }


def grant_identity_platform_admin(
    session: Session,
    *,
    provider: str,
    subject: str,
    granted_by_user_id: str | None,
    reason: str,
) -> dict:
    """Grant durable platform privilege through a separate explicit action."""
    user, _identity = _active_identity(session, provider, subject)
    if granted_by_user_id:
        actor = session.get(UserModel, granted_by_user_id)
        if actor is None or actor.status != "active":
            raise LookupError("active granting user not found")
    row = PlatformAdminService(session).grant(
        user_id=user.id,
        granted_by_user_id=granted_by_user_id,
        reason=_reason(reason),
    )
    return {"user_id": user.id, "assignment_id": row.id, "status": row.status}


@dataclass(slots=True)
class LegacyBackfillReport:
    processed: int = 0
    identities_created: int = 0
    memberships_created: int = 0
    sessions_updated: int = 0
    unresolved_count: int = 0
    next_cursor: str | None = None
    unresolved: list[dict[str, str]] = field(default_factory=list)

    def public_dict(self) -> dict:
        return asdict(self)


def backfill_legacy_auth_page(
    session: Session,
    *,
    after_id: str | None,
    page_size: int,
    actor_id: str | None,
    reason: str,
) -> LegacyBackfillReport:
    """Backfill one bounded page; email is never used to link identities."""
    if page_size < 1 or page_size > 1000:
        raise ValueError("page_size must be between 1 and 1000")
    audit_reason = _reason(reason)
    statement = select(OAuthConnectionModel).order_by(OAuthConnectionModel.id)
    if after_id:
        statement = statement.where(OAuthConnectionModel.id > after_id)
    connections = list(session.scalars(statement.limit(page_size)))
    report = LegacyBackfillReport()
    identities = IdentityResolutionService(session)
    memberships = TenantMembershipService(session)

    for connection in connections:
        report.processed += 1
        report.next_cursor = connection.id
        provider = connection.provider.strip().casefold()
        subject = connection.provider_account_id.strip()
        if provider not in {"google", "microsoft"} or not subject:
            _unresolved(report, connection, "invalid_identity")
            continue
        tenant = session.get(TenantModel, connection.tenant_id)
        if tenant is None or tenant.status != "active":
            _unresolved(report, connection, "active_tenant_missing")
            continue

        user, identity, identity_created = _resolve_connection_identity(
            session,
            identities,
            provider=provider,
            subject=subject,
            email=connection.account_email,
        )
        if identity_created:
            report.identities_created += 1
        if user is None or user.status != "active":
            _unresolved(report, connection, "active_user_missing")
            continue

        membership = memberships.get_membership(tenant.id, user.id)
        if membership is None:
            membership = memberships.add_member(
                tenant_id=tenant.id, user_id=user.id, status="active"
            )
            report.memberships_created += 1
        elif membership.status != "active":
            _unresolved(report, connection, "membership_inactive")
            continue

        sessions = list(
            session.scalars(
                select(AuthSessionModel).where(
                    AuthSessionModel.tenant_id == connection.tenant_id,
                    AuthSessionModel.connection_id == connection.id,
                    AuthSessionModel.provider == connection.provider,
                )
            )
        )
        for auth_session in sessions:
            if auth_session.user_id not in {None, user.id}:
                _unresolved(report, connection, "session_user_conflict")
                continue
            changed = False
            if auth_session.user_id is None:
                auth_session.user_id = user.id
                changed = True
            if auth_session.active_tenant_id is None:
                auth_session.active_tenant_id = tenant.id
                changed = True
            if changed:
                report.sessions_updated += 1
        session.add(
            AuthAuditEventModel(
                tenant_id=tenant.id,
                actor_id=actor_id,
                provider=provider,
                connection_id=connection.id,
                action="legacy_auth_backfilled",
                detail_json={
                    "reason": audit_reason,
                    "user_id": user.id,
                    "membership_id": membership.id,
                },
            )
        )
    session.flush()
    return report


def _resolve_connection_identity(
    session: Session,
    identities: IdentityResolutionService,
    *,
    provider: str,
    subject: str,
    email: str | None,
):
    """Create by provider subject with the database unique key as final guard."""
    identity = identities.find_by_provider_subject(provider, subject)
    if identity is not None:
        return session.get(UserModel, identity.user_id), identity, False
    if session.get_bind().dialect.name == "sqlite":
        user, identity = identities.create_user_from_identity(
            provider=provider,
            provider_subject=subject,
            provider_email=email,
        )
        return user, identity, True
    try:
        with session.begin_nested():
            user, identity = identities.create_user_from_identity(
                provider=provider,
                provider_subject=subject,
                provider_email=email,
            )
    except IntegrityError:
        identity = identities.find_by_provider_subject(provider, subject)
        if identity is None:
            raise
        user = session.get(UserModel, identity.user_id)
        return user, identity, False
    return user, identity, True


def _unresolved(
    report: LegacyBackfillReport,
    connection: OAuthConnectionModel,
    code: str,
) -> None:
    report.unresolved_count += 1
    if len(report.unresolved) < 100:
        report.unresolved.append(
            {
                "connection_id": connection.id,
                "provider": connection.provider,
                "code": code,
            }
        )
