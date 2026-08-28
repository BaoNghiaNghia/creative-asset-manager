import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import AuthAuditEventModel, UserModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.admin_service import (
    TenantAccessAdminError,
    TenantAccessAdminService,
)
from app.modules.authorization.model import MembershipRoleModel, RoleModel
from app.modules.authorization.seed import PERMISSION_DEFINITIONS, seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService


class TenantAccessAdminServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.session = self.factory()
        membership_service = TenantMembershipService(self.session)
        self.tenant = membership_service.create_tenant(name="Studio", slug="studio")
        self.other_tenant = membership_service.create_tenant(name="Other", slug="other")
        self.admin = UserModel(primary_email="admin@example.com", display_name="Admin", status="active")
        self.member = UserModel(primary_email="member@example.com", display_name="Member", avatar_url="https://lh3.googleusercontent.com/member-avatar", status="active")
        self.other = UserModel(primary_email="other@example.com", display_name="Other", status="active")
        self.session.add_all([self.admin, self.member, self.other])
        self.session.flush()
        self.admin_membership = membership_service.add_member(tenant_id=self.tenant.id, user_id=self.admin.id)
        self.other_membership = membership_service.add_member(tenant_id=self.other_tenant.id, user_id=self.other.id)
        seed_tenant_rbac(self.session, self.tenant.id)
        seed_tenant_rbac(self.session, self.other_tenant.id)
        authorization = TenantAuthorizationService(self.session)
        authorization.assign_role(
            tenant_id=self.tenant.id,
            membership_id=self.admin_membership.id,
            role_id=self.role("tenant_admin").id,
            actor_id="bootstrap",
        )
        self.session.commit()
        self.service = TenantAccessAdminService(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def role(self, key, tenant_id=None):
        return self.session.scalar(
            select(RoleModel).where(
                RoleModel.tenant_id == (tenant_id or self.tenant.id),
                RoleModel.role_key == key,
            )
        )

    def add_member(self, *, status="active"):
        return self.service.add_member(
            tenant_id=self.tenant.id,
            actor_user_id=self.admin.id,
            reason="approved membership change",
            user_id=self.member.id,
            email=None,
            status=status,
        )

    def test_list_members_is_paginated_filterable_and_safe(self):
        membership = self.add_member()
        self.service.assign_role(
            tenant_id=self.tenant.id,
            membership_id=membership.id,
            role_id=self.role("viewer").id,
            actor_user_id=self.admin.id,
            actor_permissions=frozenset(PERMISSION_DEFINITIONS),
            platform_admin=False,
            reason="viewer access",
        )
        self.session.commit()
        result = self.service.list_members(
            tenant_id=self.tenant.id,
            page=1,
            page_size=1,
            query="member@",
            role_key="viewer",
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["user_id"], self.member.id)
        self.assertEqual(result["items"][0]["roles"][0]["key"], "viewer")
        self.assertEqual(result["items"][0]["avatar_url"], "https://lh3.googleusercontent.com/member-avatar")
        serialized = str(result).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("session", serialized)

    def test_invite_existing_user_and_duplicate_invitation_conflict(self):
        invitation = self.service.add_member(
            tenant_id=self.tenant.id,
            actor_user_id=self.admin.id,
            reason="invite existing account",
            user_id=None,
            email="MEMBER@example.com",
            status="invited",
        )
        self.assertEqual(invitation.status, "invited")
        with self.assertRaises(TenantAccessAdminError) as captured:
            self.service.add_member(
                tenant_id=self.tenant.id,
                actor_user_id=self.admin.id,
                reason="duplicate invitation",
                user_id=None,
                email="member@example.com",
                status="invited",
            )
        self.assertEqual(captured.exception.code, "invitation_conflict")

    def test_membership_status_transitions_and_audit(self):
        membership = self.add_member(status="invited")
        for action, expected in (
            ("activate", "active"),
            ("suspend", "suspended"),
            ("restore", "active"),
            ("remove", "removed"),
            ("restore", "active"),
        ):
            changed = self.service.update_membership_status(
                tenant_id=self.tenant.id,
                membership_id=membership.id,
                action=action,
                actor_user_id=self.admin.id,
                reason=f"approved {action}",
                platform_admin=False,
                allow_final_admin_override=False,
            )
            self.assertEqual(changed.status, expected)
        self.session.commit()
        actions = set(self.session.scalars(select(AuthAuditEventModel.action)))
        self.assertTrue(
            {"tenant_member_invited", "tenant_member_activate", "tenant_member_suspend", "tenant_member_restore", "tenant_member_remove"}
            <= actions
        )

    def test_assign_remove_and_custom_role_lifecycle(self):
        membership = self.add_member()
        custom = self.service.create_custom_role(
            tenant_id=self.tenant.id,
            actor_user_id=self.admin.id,
            actor_permissions=frozenset(PERMISSION_DEFINITIONS),
            platform_admin=False,
            role_key="creative_reviewer",
            name="Creative reviewer",
            description="Review assets",
            permission_keys={"assets.read"},
            reason="new review role",
        )
        assignment = self.service.assign_role(
            tenant_id=self.tenant.id,
            membership_id=membership.id,
            role_id=custom.id,
            actor_user_id=self.admin.id,
            actor_permissions=frozenset(PERMISSION_DEFINITIONS),
            platform_admin=False,
            reason="assign reviewer",
        )
        self.assertIsNotNone(assignment.id)
        updated = self.service.update_custom_role(
            tenant_id=self.tenant.id,
            role_id=custom.id,
            actor_user_id=self.admin.id,
            actor_permissions=frozenset(PERMISSION_DEFINITIONS),
            platform_admin=False,
            name="Senior reviewer",
            description="Review and search",
            permission_keys={"assets.read", "search.read"},
            reason="expanded role",
        )
        self.assertEqual(updated.name, "Senior reviewer")
        self.assertTrue(
            self.service.remove_role(
                tenant_id=self.tenant.id,
                membership_id=membership.id,
                role_id=custom.id,
                actor_user_id=self.admin.id,
                reason="remove reviewer",
                platform_admin=False,
                allow_final_admin_override=False,
            )
        )
        self.service.delete_custom_role(
            tenant_id=self.tenant.id,
            role_id=custom.id,
            actor_user_id=self.admin.id,
            reason="retire role",
        )
        self.session.flush()
        self.assertIsNone(self.session.get(RoleModel, custom.id))
        audit_events = list(
            self.session.scalars(
                select(AuthAuditEventModel).where(AuthAuditEventModel.tenant_id == self.tenant.id)
            )
        )
        self.assertTrue(any(event.detail_json.get("reason") == "assign reviewer" for event in audit_events))
        self.assertTrue(any(event.detail_json.get("reason") == "retire role" for event in audit_events))


    def test_final_admin_is_protected_and_platform_override_is_explicit(self):
        with self.assertRaises(TenantAccessAdminError) as role_removal:
            self.service.remove_role(
                tenant_id=self.tenant.id,
                membership_id=self.admin_membership.id,
                role_id=self.role("tenant_admin").id,
                actor_user_id=self.admin.id,
                reason="unsafe final admin role removal",
                platform_admin=False,
                allow_final_admin_override=False,
            )
        self.assertEqual(role_removal.exception.code, "final_tenant_admin")

        with self.assertRaises(TenantAccessAdminError) as captured:
            self.service.update_membership_status(
                tenant_id=self.tenant.id,
                membership_id=self.admin_membership.id,
                action="remove",
                actor_user_id=self.admin.id,
                reason="unsafe removal",
                platform_admin=False,
                allow_final_admin_override=False,
            )
        self.assertEqual(captured.exception.code, "final_tenant_admin")
        removed = self.service.update_membership_status(
            tenant_id=self.tenant.id,
            membership_id=self.admin_membership.id,
            action="remove",
            actor_user_id=self.admin.id,
            reason="platform recovery override",
            platform_admin=True,
            allow_final_admin_override=True,
        )
        self.assertEqual(removed.status, "removed")

    def test_self_escalation_and_cross_tenant_assignment_are_rejected(self):
        membership = self.add_member()
        with self.assertRaises(TenantAccessAdminError) as captured:
            self.service.assign_role(
                tenant_id=self.tenant.id,
                membership_id=membership.id,
                role_id=self.role("tenant_admin").id,
                actor_user_id=self.member.id,
                actor_permissions=frozenset({"tenant_roles.manage"}),
                platform_admin=False,
                reason="self escalation",
            )
        self.assertEqual(captured.exception.code, "grant_authority_exceeded")
        with self.assertRaises(TenantAccessAdminError) as mismatch:
            self.service.assign_role(
                tenant_id=self.tenant.id,
                membership_id=self.other_membership.id,
                role_id=self.role("viewer").id,
                actor_user_id=self.admin.id,
                actor_permissions=frozenset(PERMISSION_DEFINITIONS),
                platform_admin=False,
                reason="cross tenant",
            )
        self.assertEqual(mismatch.exception.code, "tenant_mismatch")

    def test_protected_role_and_audit_records(self):
        with self.assertRaises(ValueError):
            self.service.create_custom_role(
                tenant_id=self.tenant.id,
                actor_user_id=self.admin.id,
                actor_permissions=frozenset(PERMISSION_DEFINITIONS),
                platform_admin=False,
                role_key="platform_admin",
                name="Forbidden platform role",
                description=None,
                permission_keys=set(),
                reason="must not be tenant scoped",
            )

        with self.assertRaises(TenantAccessAdminError) as captured:
            self.service.delete_custom_role(
                tenant_id=self.tenant.id,
                role_id=self.role("tenant_admin").id,
                actor_user_id=self.admin.id,
                reason="must remain protected",
            )
        self.assertEqual(captured.exception.code, "protected_role")
        self.add_member()
        self.session.commit()
        audit_count = self.session.scalar(
            select(func.count()).select_from(AuthAuditEventModel).where(
                AuthAuditEventModel.tenant_id == self.tenant.id
            )
        )
        self.assertGreaterEqual(audit_count, 2)


if __name__ == "__main__":
    unittest.main()
