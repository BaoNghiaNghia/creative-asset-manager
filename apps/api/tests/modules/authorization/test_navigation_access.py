import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import AuthAuditEventModel, UserModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.admin_service import TenantAccessAdminError, TenantAccessAdminService
from app.modules.authorization.model import RoleModel
from app.modules.authorization.navigation_scope import NAVIGATION_MODULES, navigation_for_api_path
from app.modules.authorization.principal import CurrentPrincipal, _require_navigation_access
from app.modules.authorization.seed import PERMISSION_DEFINITIONS, seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService
from fastapi import HTTPException


class MemberNavigationAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, class_=Session, expire_on_commit=False)()
        membership = TenantMembershipService(self.session)
        self.tenant = membership.create_tenant(name="Workspace", slug="nav-workspace")
        self.other_tenant = membership.create_tenant(name="Other", slug="nav-other")
        self.admin = UserModel(primary_email="admin@sample.test", status="active")
        self.user = UserModel(primary_email="member@sample.test", status="active")
        self.session.add_all([self.admin, self.user])
        self.session.flush()
        self.admin_membership = membership.add_member(tenant_id=self.tenant.id, user_id=self.admin.id)
        self.member = membership.add_member(tenant_id=self.tenant.id, user_id=self.user.id)
        seed_tenant_rbac(self.session, self.tenant.id)
        admin_role = self.session.scalar(select(RoleModel).where(RoleModel.tenant_id == self.tenant.id, RoleModel.role_key == "tenant_admin"))
        viewer_role = self.session.scalar(select(RoleModel).where(RoleModel.tenant_id == self.tenant.id, RoleModel.role_key == "viewer"))
        authorizer = TenantAuthorizationService(self.session)
        authorizer.assign_role(tenant_id=self.tenant.id, membership_id=self.admin_membership.id, role_id=admin_role.id)
        authorizer.assign_role(tenant_id=self.tenant.id, membership_id=self.member.id, role_id=viewer_role.id)
        self.session.commit()
        self.admin_service = TenantAccessAdminService(self.session)
        self.authz = TenantAuthorizationService(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def change(self, modules, actor_permissions=None, member_id=None, actor_id=None):
        return self.admin_service.update_member_navigation_modules(
            tenant_id=self.tenant.id, membership_id=member_id or self.member.id,
            actor_user_id=actor_id or self.admin.id,
            actor_permissions=frozenset(PERMISSION_DEFINITIONS) if actor_permissions is None else actor_permissions,
            platform_admin=False, modules=modules, reason="approved navbar assignment",
        )

    def test_navigation_grant_replaces_viewer_privileges_and_restores_legacy(self):
        before = self.authz.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertIsNone(before.navigation_modules)
        self.assertIn("assets.upload", before.permissions)
        self.assertEqual(self.change(["inventory", "review-board"]), ["inventory", "review-board"])
        restricted = self.authz.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(restricted.navigation_modules, {"inventory", "review-board"})
        self.assertIn("inventory.finalize", restricted.permissions)
        self.assertIn("public_review.manage", restricted.permissions)
        self.assertNotIn("assets.upload", restricted.permissions)
        self.assertNotIn("tenant_members.manage", restricted.permissions)
        self.assertNotIn("ai_provider.configure", restricted.permissions)
        self.assertEqual(self.change([]), [])
        empty = self.authz.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(empty.permissions, frozenset())
        self.assertEqual(empty.navigation_modules, frozenset())
        self.assertIsNone(self.change(None))
        restored = self.authz.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertIsNone(restored.navigation_modules)
        self.assertIn("assets.upload", restored.permissions)
        self.session.commit()
        self.assertIn("member_navigation_access_replaced", set(self.session.scalars(select(AuthAuditEventModel.action))))

    def test_scope_validates_tenant_target_authority_and_admin_lockout(self):
        with self.assertRaises(ValueError):
            self.change(["inventory", "inventory"])
        with self.assertRaises(ValueError):
            self.change(["platform-admin"])
        with self.assertRaises(TenantAccessAdminError) as insufficient:
            self.change(["operations"], actor_permissions=frozenset({"tenant_members.manage", "tenant_roles.manage"}))
        self.assertEqual(insufficient.exception.code, "grant_authority_exceeded")
        self.change(["inventory"])
        with self.assertRaises(TenantAccessAdminError) as legacy_reset:
            self.change(None, actor_permissions=frozenset({"tenant_members.manage", "tenant_roles.manage"}))
        self.assertEqual(legacy_reset.exception.code, "grant_authority_exceeded")
        with self.assertRaises(TenantAccessAdminError) as wrong_membership:
            self.change(["inventory"], member_id=self.other_tenant.id)
        self.assertEqual(wrong_membership.exception.code, "membership_not_found")
        with self.assertRaises(TenantAccessAdminError) as self_scope:
            self.change(["inventory"], member_id=self.admin_membership.id)
        self.assertEqual(self_scope.exception.code, "self_scope_forbidden")
        with self.assertRaises(TenantAccessAdminError) as protect:
            self.change(["inventory"], member_id=self.admin_membership.id, actor_id=self.user.id)
        self.assertEqual(protect.exception.code, "protected_admin_scope")

    def test_api_routes_enforce_separate_branding_and_operations_scope(self):
        self.assertEqual(navigation_for_api_path("/api/v1/branding-dna/scan"), "branding-dna")
        self.assertEqual(navigation_for_api_path("/api/v1/admin/ai-operations/jobs"), "queue")
        self.assertEqual(navigation_for_api_path("/api/v1/admin/ai-operations/providers"), "operations")
        self.assertEqual(navigation_for_api_path("/api/v1/tenants/tenant-x/members"), "access")
        self.assertIsNone(navigation_for_api_path("/api/public/review/share-key"))
        principal = CurrentPrincipal(
            user_id=self.user.id, active_tenant_id=self.tenant.id, membership_id=self.member.id,
            external_identity=None, effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure"}),
            platform_admin=False, session_id="session", authorization_source="tenant_rbac",
            navigation_modules=frozenset({"branding-dna"}),
        )
        _require_navigation_access(principal, "/api/v1/branding-dna/scan")
        with self.assertRaises(HTTPException) as denied:
            _require_navigation_access(principal, "/api/v1/admin/ai-operations/providers")
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(denied.exception.detail["code"], "navigation_access_required")
        _require_navigation_access(principal, "/api/v1/auth/identity")


if __name__ == "__main__":
    unittest.main()
