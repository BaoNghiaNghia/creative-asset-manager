from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import AuthAuditEventModel, TenantMembershipModel
from app.modules.auth_persistence.tenant_membership import TenantAccessError, TenantMembershipService
from app.modules.authorization.model import MembershipRoleModel, PermissionModel, RoleModel, RolePermissionModel

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_RESERVED_ROLE_KEYS = {"viewer", "operator", "tenant_admin", "billing_admin", "platform_admin"}


class AuthorizationError(PermissionError):
    def __init__(self, code: str, message: str, *, required_permission: str | None = None):
        super().__init__(message)
        self.code = code
        self.required_permission = required_permission


@dataclass(frozen=True, slots=True)
class EffectiveAuthorization:
    tenant_id: str
    membership_id: str
    roles: frozenset[str]
    permissions: frozenset[str]


class TenantAuthorizationService:
    def __init__(self, session: Session):
        self.session = session
        self.memberships = TenantMembershipService(session)

    def get_effective_permissions(self, *, tenant_id: str, user_id: str) -> EffectiveAuthorization:
        try:
            membership = self.memberships.require_active_membership(tenant_id, user_id)
        except TenantAccessError:
            return EffectiveAuthorization(tenant_id, "", frozenset(), frozenset())
        rows = self.session.execute(
            select(RoleModel.role_key, PermissionModel.permission_key)
            .select_from(MembershipRoleModel)
            .join(RoleModel, RoleModel.id == MembershipRoleModel.role_id)
            .join(RolePermissionModel, RolePermissionModel.role_id == RoleModel.id)
            .join(PermissionModel, PermissionModel.id == RolePermissionModel.permission_id)
            .where(
                MembershipRoleModel.tenant_id == tenant_id,
                MembershipRoleModel.tenant_membership_id == membership.id,
                RoleModel.tenant_id == tenant_id,
                RoleModel.status == "active",
                PermissionModel.status == "active",
            )
        ).all()
        return EffectiveAuthorization(
            tenant_id=tenant_id,
            membership_id=membership.id,
            roles=frozenset(row[0] for row in rows),
            permissions=frozenset(row[1] for row in rows),
        )

    def has_permission(self, *, tenant_id: str, user_id: str, permission_key: str) -> bool:
        return permission_key in self.get_effective_permissions(
            tenant_id=tenant_id, user_id=user_id
        ).permissions

    def require_permission(self, *, tenant_id: str, user_id: str, permission_key: str) -> EffectiveAuthorization:
        effective = self.get_effective_permissions(tenant_id=tenant_id, user_id=user_id)
        if permission_key not in effective.permissions:
            raise AuthorizationError(
                "permission_required",
                "required tenant permission is missing",
                required_permission=permission_key,
            )
        return effective

    def assign_role(self, *, tenant_id: str, membership_id: str, role_id: str, actor_id: str | None = None) -> MembershipRoleModel:
        membership, role = self._compatible_active_records(tenant_id, membership_id, role_id)
        existing = self.session.scalar(select(MembershipRoleModel).where(
            MembershipRoleModel.tenant_membership_id == membership.id,
            MembershipRoleModel.role_id == role.id,
        ))
        if existing is not None:
            return existing
        try:
            with self.session.begin_nested():
                assignment = MembershipRoleModel(
                    tenant_id=tenant_id,
                    tenant_membership_id=membership.id,
                    role_id=role.id,
                )
                self.session.add(assignment)
                self.session.flush()
        except IntegrityError:
            assignment = self.session.scalar(select(MembershipRoleModel).where(
                MembershipRoleModel.tenant_membership_id == membership.id,
                MembershipRoleModel.role_id == role.id,
            ))
            if assignment is None:
                raise
        self._audit("tenant_role_assigned", tenant_id, actor_id, membership.id, role.id)
        return assignment

    def remove_role(self, *, tenant_id: str, membership_id: str, role_id: str, actor_id: str | None = None) -> bool:
        self._compatible_records(tenant_id, membership_id, role_id)
        assignment = self.session.scalar(select(MembershipRoleModel).where(
            MembershipRoleModel.tenant_id == tenant_id,
            MembershipRoleModel.tenant_membership_id == membership_id,
            MembershipRoleModel.role_id == role_id,
        ))
        if assignment is None:
            return False
        self.session.delete(assignment)
        self._audit("tenant_role_removed", tenant_id, actor_id, membership_id, role_id)
        self.session.flush()
        return True

    def create_custom_role(self, *, tenant_id: str, role_key: str, name: str, permission_keys: set[str], description: str | None = None, actor_id: str | None = None) -> RoleModel:
        normalized_key = role_key.strip().lower()
        if normalized_key in _RESERVED_ROLE_KEYS or not _KEY_PATTERN.fullmatch(normalized_key):
            raise ValueError("custom role key is invalid or reserved")
        normalized_name = name.strip()[:255]
        if not normalized_name:
            raise ValueError("custom role name must not be empty")
        permissions = list(self.session.scalars(select(PermissionModel).where(
            PermissionModel.permission_key.in_(permission_keys),
            PermissionModel.status == "active",
        ))) if permission_keys else []
        if {item.permission_key for item in permissions} != set(permission_keys):
            raise ValueError("custom role contains unknown permission")
        role = RoleModel(
            tenant_id=tenant_id,
            role_key=normalized_key,
            name=normalized_name,
            description=(description or "")[:2000] or None,
            is_system=False,
            protected=False,
            status="active",
        )
        self.session.add(role)
        self.session.flush()
        for permission in permissions:
            self.session.add(RolePermissionModel(role_id=role.id, permission_id=permission.id))
        self._audit("tenant_custom_role_created", tenant_id, actor_id, None, role.id)
        self.session.flush()
        return role

    def delete_role(self, *, tenant_id: str, role_id: str, actor_id: str | None = None) -> bool:
        role = self.session.get(RoleModel, role_id)
        if role is None or role.tenant_id != tenant_id:
            raise AuthorizationError("tenant_mismatch", "role does not belong to tenant")
        if role.protected or role.is_system:
            raise AuthorizationError("protected_role", "system role is protected")
        self.session.delete(role)
        self._audit("tenant_custom_role_deleted", tenant_id, actor_id, None, role.id)
        self.session.flush()
        return True

    def _compatible_active_records(self, tenant_id: str, membership_id: str, role_id: str):
        membership, role = self._compatible_records(tenant_id, membership_id, role_id)
        if membership.status != "active" or role.status != "active":
            raise AuthorizationError("inactive_assignment_target", "membership and role must be active")
        self.memberships.require_active_membership(tenant_id, membership.user_id)
        return membership, role

    def _compatible_records(self, tenant_id: str, membership_id: str, role_id: str):
        membership = self.session.get(TenantMembershipModel, membership_id)
        role = self.session.get(RoleModel, role_id)
        if membership is None or role is None:
            raise LookupError("membership or role not found")
        if membership.tenant_id != tenant_id or role.tenant_id != tenant_id:
            raise AuthorizationError("tenant_mismatch", "membership and role must belong to tenant")
        return membership, role

    def _audit(self, action: str, tenant_id: str, actor_id: str | None, membership_id: str | None, role_id: str) -> None:
        self.session.add(AuthAuditEventModel(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            detail_json={"membership_id": membership_id, "role_id": role_id},
        ))
