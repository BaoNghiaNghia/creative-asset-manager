from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    AuthSessionModel,
    TenantMembershipModel,
    TenantModel,
    UserModel,
)

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


class TenantAccessError(PermissionError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: str | None
    tenant_id: str
    membership_id: str | None
    legacy: bool = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_slug(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.strip().casefold()).strip("-")
    if not slug:
        raise ValueError("tenant slug must not be empty")
    return slug[:255]


class TenantMembershipService:
    def __init__(self, session: Session):
        self.session = session

    def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        tenant_id: str | None = None,
    ) -> TenantModel:
        normalized_name = name.strip()[:255]
        if not normalized_name:
            raise ValueError("tenant name must not be empty")
        row = TenantModel(
            id=tenant_id or str(uuid4()),
            name=normalized_name,
            slug=normalize_slug(slug),
            status="active",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_user_tenants(
        self, user_id: str, *, include_inactive: bool = False
    ) -> list[tuple[TenantMembershipModel, TenantModel]]:
        statement = (
            select(TenantMembershipModel, TenantModel)
            .join(TenantModel, TenantModel.id == TenantMembershipModel.tenant_id)
            .where(TenantMembershipModel.user_id == user_id)
            .order_by(TenantModel.name, TenantModel.id)
        )
        if not include_inactive:
            statement = statement.where(
                TenantMembershipModel.status == "active",
                TenantModel.status == "active",
            )
        return list(self.session.execute(statement))

    def get_membership(
        self, tenant_id: str, user_id: str
    ) -> TenantMembershipModel | None:
        return self.session.scalar(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.user_id == user_id,
            )
        )

    def require_active_membership(
        self, tenant_id: str, user_id: str
    ) -> TenantMembershipModel:
        user = self.session.get(UserModel, user_id)
        tenant = self.session.get(TenantModel, tenant_id)
        membership = self.get_membership(tenant_id, user_id)
        if user is None or user.status != "active":
            raise TenantAccessError("inactive_user", "application user is not active")
        if tenant is None:
            raise TenantAccessError("tenant_not_found", "tenant was not found")
        if tenant.status != "active":
            raise TenantAccessError("inactive_tenant", "tenant is not active")
        if membership is None or membership.status != "active":
            raise TenantAccessError(
                "inactive_membership", "active tenant membership is required"
            )
        return membership

    def add_member(
        self,
        *,
        tenant_id: str,
        user_id: str,
        invited_by_user_id: str | None = None,
        status: str = "active",
    ) -> TenantMembershipModel:
        if status not in {"invited", "active"}:
            raise ValueError("new membership status must be invited or active")
        if self.session.get(TenantModel, tenant_id) is None:
            raise LookupError("tenant not found")
        if self.session.get(UserModel, user_id) is None:
            raise LookupError("user not found")
        existing = self.get_membership(tenant_id, user_id)
        if existing is not None:
            return existing
        now = utcnow()
        try:
            with self.session.begin_nested():
                membership = TenantMembershipModel(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    status=status,
                    joined_at=now if status == "active" else None,
                    invited_by_user_id=invited_by_user_id,
                )
                self.session.add(membership)
                self.session.flush()
        except IntegrityError:
            membership = self.get_membership(tenant_id, user_id)
            if membership is None:
                raise
        return membership

    def suspend_member(
        self, tenant_id: str, user_id: str
    ) -> TenantMembershipModel:
        membership = self._required_membership(tenant_id, user_id)
        membership.status = "suspended"
        membership.updated_at = utcnow()
        self.session.flush()
        return membership

    def remove_member(
        self, tenant_id: str, user_id: str
    ) -> TenantMembershipModel:
        membership = self._required_membership(tenant_id, user_id)
        membership.status = "removed"
        membership.updated_at = utcnow()
        self.session.flush()
        return membership

    def restore_member(
        self, tenant_id: str, user_id: str
    ) -> TenantMembershipModel:
        membership = self._required_membership(tenant_id, user_id)
        membership.status = "active"
        membership.joined_at = membership.joined_at or utcnow()
        membership.updated_at = utcnow()
        self.session.flush()
        return membership

    def select_active_tenant(
        self,
        *,
        session_id_hash: str,
        user_id: str,
        tenant_id: str,
    ) -> TenantContext:
        membership = self.require_active_membership(tenant_id, user_id)
        auth_session = self.session.get(AuthSessionModel, session_id_hash)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.user_id != user_id
        ):
            raise TenantAccessError("invalid_session", "session is not active")
        auth_session.active_tenant_id = tenant_id
        self.session.add(AuthAuditEventModel(
            tenant_id=tenant_id,
            actor_id=user_id,
            session_id_hash=session_id_hash,
            action="active_tenant_selected",
            detail_json={"membership_id": membership.id},
        ))
        self.session.flush()
        return TenantContext(user_id, tenant_id, membership.id)

    def resolve_active_tenant(self, cloud_session) -> TenantContext:
        if cloud_session.user_id is None:
            return TenantContext(None, cloud_session.tenant_id, None, legacy=True)
        if cloud_session.active_tenant_id:
            membership = self.require_active_membership(
                cloud_session.active_tenant_id, cloud_session.user_id
            )
            return TenantContext(
                cloud_session.user_id,
                cloud_session.active_tenant_id,
                membership.id,
            )
        memberships = self.list_user_tenants(cloud_session.user_id)
        if len(memberships) != 1:
            code = "active_tenant_required" if memberships else "membership_required"
            raise TenantAccessError(code, "an explicit active tenant is required")
        membership, tenant = memberships[0]
        auth_session = self.session.get(
            AuthSessionModel, cloud_session.session_id_hash
        )
        if auth_session is not None:
            auth_session.active_tenant_id = tenant.id
            self.session.flush()
        return TenantContext(cloud_session.user_id, tenant.id, membership.id)

    def ensure_development_personal_tenant(
        self,
        *,
        settings: Settings,
        user: UserModel,
        legacy_tenant_id: str,
        display_name: str | None,
    ) -> TenantModel | None:
        if not settings.DEVELOPMENT_PERSONAL_TENANT_ENABLED:
            return None
        if settings.APP_ENV.strip().casefold() not in {
            "development",
            "dev",
            "local",
            "test",
        }:
            raise ValueError(
                "development personal tenant bootstrap is forbidden in production"
            )
        tenant = self.session.get(TenantModel, legacy_tenant_id)
        if tenant is None:
            slug = normalize_slug(f"personal-{user.id}")
            tenant = self.session.scalar(
                select(TenantModel).where(TenantModel.slug == slug)
            )
            if tenant is None:
                tenant = self.create_tenant(
                    tenant_id=legacy_tenant_id,
                    name=f"{display_name or 'Personal'} workspace",
                    slug=slug,
                )
        membership = self.get_membership(tenant.id, user.id)
        if membership is None:
            self.add_member(
                tenant_id=tenant.id, user_id=user.id, status="active"
            )
        elif membership.status != "active":
            raise TenantAccessError(
                "inactive_membership",
                "development personal tenant membership is not active",
            )
        return tenant

    def _required_membership(
        self, tenant_id: str, user_id: str
    ) -> TenantMembershipModel:
        membership = self.get_membership(tenant_id, user_id)
        if membership is None:
            raise LookupError("tenant membership not found")
        return membership
