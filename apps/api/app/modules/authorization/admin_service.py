from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.modules.auth_persistence.identity import normalize_email
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    TenantMembershipModel,
    TenantModel,
    UserModel,
)
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.principal_cache import principal_cache
from app.modules.authorization.model import (
    MembershipRoleModel,
    PermissionModel,
    RoleModel,
    RolePermissionModel,
)
from app.modules.authorization.service import TenantAuthorizationService


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TenantAccessAdminError(PermissionError):
    def __init__(self, code: str, message: str, *, status_code: int = 403):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TenantAccessAdminService:
    """Tenant-scoped membership and role administration.

    Callers must still enforce FastAPI permissions. This service owns tenant
    predicates, state transitions, grant-authority and final-admin invariants.
    """

    def __init__(self, session: Session):
        self.session = session
        self.memberships = TenantMembershipService(session)
        self.authorization = TenantAuthorizationService(session)

    def list_members(
        self,
        *,
        tenant_id: str,
        page: int,
        page_size: int,
        query: str | None = None,
        status: str | None = None,
        role_key: str | None = None,
    ) -> dict:
        statement = (
            select(TenantMembershipModel, UserModel)
            .join(UserModel, UserModel.id == TenantMembershipModel.user_id)
            .where(TenantMembershipModel.tenant_id == tenant_id)
        )
        if status:
            statement = statement.where(TenantMembershipModel.status == status)
        if query:
            pattern = f"%{query.strip().casefold()}%"
            statement = statement.where(
                or_(
                    func.lower(func.coalesce(UserModel.display_name, "")).like(pattern),
                    func.lower(func.coalesce(UserModel.primary_email, "")).like(pattern),
                )
            )
        if role_key:
            matching_memberships = (
                select(MembershipRoleModel.tenant_membership_id)
                .join(RoleModel, RoleModel.id == MembershipRoleModel.role_id)
                .where(
                    MembershipRoleModel.tenant_id == tenant_id,
                    RoleModel.tenant_id == tenant_id,
                    RoleModel.role_key == role_key,
                )
            )
            statement = statement.where(TenantMembershipModel.id.in_(matching_memberships))
        total = self.session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        rows = list(
            self.session.execute(
                statement.order_by(
                    func.lower(func.coalesce(UserModel.display_name, UserModel.primary_email, "")),
                    TenantMembershipModel.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        role_map: dict[str, list[dict]] = {membership.id: [] for membership, _user in rows}
        if role_map:
            role_rows = self.session.execute(
                select(MembershipRoleModel.tenant_membership_id, RoleModel)
                .join(RoleModel, RoleModel.id == MembershipRoleModel.role_id)
                .where(
                    MembershipRoleModel.tenant_id == tenant_id,
                    MembershipRoleModel.tenant_membership_id.in_(role_map),
                    RoleModel.tenant_id == tenant_id,
                )
                .order_by(RoleModel.name, RoleModel.id)
            )
            for membership_id, role in role_rows:
                role_map[membership_id].append(self._role_summary(role))
        return {
            "items": [
                {
                    "membership_id": membership.id,
                    "user_id": user.id,
                    "display_name": user.display_name,
                    "email": user.primary_email,
                    "avatar_url": user.avatar_url,
                    "status": membership.status,
                    "roles": role_map[membership.id],
                    "joined_at": membership.joined_at,
                    "last_login_at": user.last_login_at,
                }
                for membership, user in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def add_member(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        reason: str,
        user_id: str | None,
        email: str | None,
        status: str,
    ) -> TenantMembershipModel:
        self._lock_tenant(tenant_id)
        user = self._resolve_user(
            user_id=user_id,
            email=email,
            create_invited_user=status == "invited",
        )
        if user.status != "active":
            raise TenantAccessAdminError("user_inactive", "User is not active", status_code=409)
        existing = self.memberships.get_membership(tenant_id, user.id)
        if existing is not None:
            code = "invitation_conflict" if existing.status == "invited" else "membership_exists"
            raise TenantAccessAdminError(code, "Tenant membership already exists", status_code=409)
        membership = self.memberships.add_member(
            tenant_id=tenant_id,
            user_id=user.id,
            invited_by_user_id=actor_user_id,
            status=status,
        )
        self._audit(
            "tenant_member_invited" if status == "invited" else "tenant_member_added",
            tenant_id,
            actor_user_id,
            reason,
            {"membership_id": membership.id, "user_id": user.id, "old_status": None, "new_status": status},
        )
        return membership

    def update_membership_status(
        self,
        *,
        tenant_id: str,
        membership_id: str,
        action: str,
        actor_user_id: str,
        reason: str,
        platform_admin: bool,
        allow_final_admin_override: bool,
    ) -> TenantMembershipModel:
        self._lock_tenant(tenant_id)
        membership = self._membership(tenant_id, membership_id)
        old_status = membership.status
        transitions = {
            "activate": ({"invited"}, "active"),
            "suspend": ({"active"}, "suspended"),
            "restore": ({"suspended", "removed"}, "active"),
            "remove": ({"invited", "active", "suspended"}, "removed"),
        }
        allowed, new_status = transitions[action]
        if old_status not in allowed:
            raise TenantAccessAdminError(
                "invalid_membership_transition",
                f"Cannot {action} membership in {old_status} state",
                status_code=409,
            )
        if action in {"suspend", "remove"}:
            self._protect_final_admin(
                tenant_id=tenant_id,
                membership_id=membership.id,
                platform_admin=platform_admin,
                allow_override=allow_final_admin_override,
            )
        membership.status = new_status
        membership.joined_at = membership.joined_at or (utcnow() if new_status == "active" else None)
        membership.updated_at = utcnow()
        self._audit(
            f"tenant_member_{action}",
            tenant_id,
            actor_user_id,
            reason,
            {
                "membership_id": membership.id,
                "user_id": membership.user_id,
                "old_status": old_status,
                "new_status": new_status,
                "final_admin_override": bool(platform_admin and allow_final_admin_override),
            },
        )
        self.session.flush()
        return membership

    def list_permissions(self) -> list[dict]:
        rows = self.session.scalars(
            select(PermissionModel)
            .where(PermissionModel.status == "active")
            .order_by(PermissionModel.permission_key)
        )
        return [
            {"id": item.id, "key": item.permission_key, "description": item.description}
            for item in rows
        ]

    def list_roles(
        self,
        *,
        tenant_id: str,
        page: int,
        page_size: int,
        query: str | None = None,
        status: str | None = None,
    ) -> dict:
        statement = select(RoleModel).where(RoleModel.tenant_id == tenant_id)
        if status:
            statement = statement.where(RoleModel.status == status)
        if query:
            pattern = f"%{query.strip().casefold()}%"
            statement = statement.where(
                or_(
                    func.lower(RoleModel.name).like(pattern),
                    func.lower(RoleModel.role_key).like(pattern),
                )
            )
        total = self.session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        roles = list(
            self.session.scalars(
                statement.order_by(RoleModel.is_system.desc(), RoleModel.name, RoleModel.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        permission_map = self._role_permission_map([role.id for role in roles])
        return {
            "items": [self._role_detail(role, permission_map.get(role.id, [])) for role in roles],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    def create_custom_role(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        actor_permissions: frozenset[str],
        platform_admin: bool,
        role_key: str,
        name: str,
        description: str | None,
        permission_keys: set[str],
        reason: str,
    ) -> RoleModel:
        self._lock_tenant(tenant_id)
        self._assert_grant_authority(permission_keys, actor_permissions, platform_admin)
        return self.authorization.create_custom_role(
            tenant_id=tenant_id,
            role_key=role_key,
            name=name,
            description=description,
            permission_keys=permission_keys,
            actor_id=actor_user_id,
            reason=reason,
        )

    def update_custom_role(
        self,
        *,
        tenant_id: str,
        role_id: str,
        actor_user_id: str,
        actor_permissions: frozenset[str],
        platform_admin: bool,
        name: str,
        description: str | None,
        permission_keys: set[str],
        reason: str,
    ) -> RoleModel:
        self._lock_tenant(tenant_id)
        role = self._role(tenant_id, role_id)
        if role.protected or role.is_system:
            raise TenantAccessAdminError("protected_role", "System role is protected", status_code=409)
        self._assert_grant_authority(permission_keys, actor_permissions, platform_admin)
        normalized_name = name.strip()[:255]
        if not normalized_name:
            raise TenantAccessAdminError("invalid_role", "Role name is required", status_code=422)
        permissions = self._permissions(permission_keys)
        old = {
            "name": role.name,
            "description": role.description,
            "permission_keys": self._role_permission_map([role.id]).get(role.id, []),
        }
        role.name = normalized_name
        role.description = (description or "")[:2000] or None
        role.updated_at = utcnow()
        self.session.execute(delete(RolePermissionModel).where(RolePermissionModel.role_id == role.id))
        self.session.add_all(
            RolePermissionModel(role_id=role.id, permission_id=permission.id)
            for permission in permissions
        )
        self._audit(
            "tenant_custom_role_updated",
            tenant_id,
            actor_user_id,
            reason,
            {
                "role_id": role.id,
                "old": old,
                "new": {"name": role.name, "description": role.description, "permission_keys": sorted(permission_keys)},
            },
        )
        self.session.flush()
        principal_cache.invalidate_tenant(tenant_id)
        return role

    def delete_custom_role(
        self, *, tenant_id: str, role_id: str, actor_user_id: str, reason: str
    ) -> None:
        self._lock_tenant(tenant_id)
        role = self._role(tenant_id, role_id)
        if role.protected or role.is_system:
            raise TenantAccessAdminError("protected_role", "System role is protected", status_code=409)
        self.authorization.delete_role(
            tenant_id=tenant_id, role_id=role.id, actor_id=actor_user_id, reason=reason
        )

    def assign_role(
        self,
        *,
        tenant_id: str,
        membership_id: str,
        role_id: str,
        actor_user_id: str,
        actor_permissions: frozenset[str],
        platform_admin: bool,
        reason: str,
    ) -> MembershipRoleModel:
        self._lock_tenant(tenant_id)
        membership = self._membership(tenant_id, membership_id)
        role = self._role(tenant_id, role_id)
        if role.role_key == "platform_admin":
            raise TenantAccessAdminError("platform_admin_forbidden", "Platform admin cannot be granted here")
        permission_keys = set(self._role_permission_map([role.id]).get(role.id, []))
        self._assert_grant_authority(permission_keys, actor_permissions, platform_admin)
        assignment = self.authorization.assign_role(
            tenant_id=tenant_id,
            membership_id=membership.id,
            role_id=role.id,
            actor_id=actor_user_id,
            reason=reason,
        )
        return assignment

    def remove_role(
        self,
        *,
        tenant_id: str,
        membership_id: str,
        role_id: str,
        actor_user_id: str,
        reason: str,
        platform_admin: bool,
        allow_final_admin_override: bool,
    ) -> bool:
        self._lock_tenant(tenant_id)
        membership = self._membership(tenant_id, membership_id)
        role = self._role(tenant_id, role_id)
        if role.role_key == "tenant_admin":
            self._protect_final_admin(
                tenant_id=tenant_id,
                membership_id=membership.id,
                platform_admin=platform_admin,
                allow_override=allow_final_admin_override,
            )
        return self.authorization.remove_role(
            tenant_id=tenant_id,
            membership_id=membership.id,
            role_id=role.id,
            actor_id=actor_user_id,
            reason=reason,
        )


    def _resolve_user(
        self,
        *,
        user_id: str | None,
        email: str | None,
        create_invited_user: bool = False,
    ) -> UserModel:
        if bool(user_id) == bool(email):
            raise TenantAccessAdminError(
                "invalid_member_target",
                "Provide exactly one of user_id or email",
                status_code=422,
            )

        if user_id:
            user = self.session.get(UserModel, user_id)

            if user is None:
                raise TenantAccessAdminError(
                    "user_not_found",
                    "User was not found",
                    status_code=404,
                )

            return user

        normalized = normalize_email(email)

        if (
            not normalized
            or "@" not in normalized
            or normalized.startswith("@")
            or normalized.endswith("@")
        ):
            raise TenantAccessAdminError(
                "invalid_email",
                "A valid email address is required",
                status_code=422,
            )

        matches = list(
            self.session.scalars(
                select(UserModel)
                .where(UserModel.primary_email == normalized)
                .limit(2)
            )
        )

        if len(matches) > 1:
            raise TenantAccessAdminError(
                "ambiguous_user",
                (
                    "More than one application user has this email; "
                    "use user_id"
                ),
                status_code=409,
            )

        if matches:
            return matches[0]

        if not create_invited_user:
            raise TenantAccessAdminError(
                "user_not_found",
                (
                    "No existing application user matches this email; "
                    "email delivery is not configured"
                ),
                status_code=404,
            )

        # This is a non-authenticated placeholder. It cannot enter a tenant
        # until a verified external identity claims the invited membership.
        user = UserModel(
            primary_email=normalized,
            display_name=None,
            avatar_url=None,
            status="active",
        )

        self.session.add(user)
        self.session.flush()

        return user
    def _lock_tenant(self, tenant_id: str) -> TenantModel:
        tenant = self.session.scalar(
            select(TenantModel).where(TenantModel.id == tenant_id).with_for_update()
        )
        if tenant is None:
            raise TenantAccessAdminError("tenant_not_found", "Tenant was not found", status_code=404)
        if tenant.status != "active":
            raise TenantAccessAdminError("tenant_inactive", "Tenant is not active", status_code=409)
        return tenant

    def _membership(self, tenant_id: str, membership_id: str) -> TenantMembershipModel:
        membership = self.session.get(TenantMembershipModel, membership_id)
        if membership is None:
            raise TenantAccessAdminError("membership_not_found", "Membership was not found", status_code=404)
        if membership.tenant_id != tenant_id:
            raise TenantAccessAdminError("tenant_mismatch", "Membership belongs to another tenant")
        return membership

    def _role(self, tenant_id: str, role_id: str) -> RoleModel:
        role = self.session.get(RoleModel, role_id)
        if role is None:
            raise TenantAccessAdminError("role_not_found", "Role was not found", status_code=404)
        if role.tenant_id != tenant_id:
            raise TenantAccessAdminError("tenant_mismatch", "Role belongs to another tenant")
        return role

    def _permissions(self, keys: set[str]) -> list[PermissionModel]:
        permissions = list(
            self.session.scalars(
                select(PermissionModel).where(
                    PermissionModel.permission_key.in_(keys),
                    PermissionModel.status == "active",
                )
            )
        ) if keys else []
        if {permission.permission_key for permission in permissions} != keys:
            raise TenantAccessAdminError("invalid_permission", "Unknown permission", status_code=422)
        return permissions

    def _assert_grant_authority(
        self,
        requested: set[str],
        actor_permissions: frozenset[str],
        platform_admin: bool,
    ) -> None:
        self._permissions(requested)
        if not platform_admin and not requested.issubset(actor_permissions):
            raise TenantAccessAdminError(
                "grant_authority_exceeded",
                "Role contains permissions the actor cannot grant",
            )

    def _protect_final_admin(
        self,
        *,
        tenant_id: str,
        membership_id: str,
        platform_admin: bool,
        allow_override: bool,
    ) -> None:
        target_is_admin = self.session.scalar(
            select(func.count())
            .select_from(MembershipRoleModel)
            .join(RoleModel, RoleModel.id == MembershipRoleModel.role_id)
            .where(
                MembershipRoleModel.tenant_id == tenant_id,
                MembershipRoleModel.tenant_membership_id == membership_id,
                RoleModel.tenant_id == tenant_id,
                RoleModel.role_key == "tenant_admin",
                RoleModel.status == "active",
            )
        )
        if not target_is_admin:
            return
        active_admin_count = self.session.scalar(
            select(func.count(func.distinct(MembershipRoleModel.tenant_membership_id)))
            .select_from(MembershipRoleModel)
            .join(RoleModel, RoleModel.id == MembershipRoleModel.role_id)
            .join(TenantMembershipModel, TenantMembershipModel.id == MembershipRoleModel.tenant_membership_id)
            .where(
                MembershipRoleModel.tenant_id == tenant_id,
                RoleModel.tenant_id == tenant_id,
                RoleModel.role_key == "tenant_admin",
                RoleModel.status == "active",
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.status == "active",
            )
        ) or 0
        if active_admin_count <= 1 and not (platform_admin and allow_override):
            raise TenantAccessAdminError(
                "final_tenant_admin", "The final tenant administrator cannot be removed", status_code=409
            )

    def _role_permission_map(self, role_ids: list[str]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {role_id: [] for role_id in role_ids}
        if not role_ids:
            return result
        rows = self.session.execute(
            select(RolePermissionModel.role_id, PermissionModel.permission_key)
            .join(PermissionModel, PermissionModel.id == RolePermissionModel.permission_id)
            .where(RolePermissionModel.role_id.in_(role_ids))
            .order_by(PermissionModel.permission_key)
        )
        for role_id, permission_key in rows:
            result[role_id].append(permission_key)
        return result

    @staticmethod
    def _role_summary(role: RoleModel) -> dict:
        return {"id": role.id, "key": role.role_key, "name": role.name, "system": role.is_system}

    def _role_detail(self, role: RoleModel, permissions: list[str]) -> dict:
        return {
            **self._role_summary(role),
            "description": role.description,
            "protected": role.protected,
            "status": role.status,
            "permissions": permissions,
            "created_at": role.created_at,
            "updated_at": role.updated_at,
        }

    def _audit(
        self,
        action: str,
        tenant_id: str,
        actor_user_id: str,
        reason: str,
        detail: dict,
    ) -> None:
        self.session.add(
            AuthAuditEventModel(
                tenant_id=tenant_id,
                actor_id=actor_user_id,
                action=action,
                detail_json={**detail, "reason": reason[:1000]},
            )
        )
