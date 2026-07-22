import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import TenantMembershipModel, UserModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.model import MembershipRoleModel, PermissionModel, RoleModel
from app.modules.authorization.seed import PERMISSION_DEFINITIONS, seed_tenant_rbac
from app.modules.authorization.service import AuthorizationError, TenantAuthorizationService


class TenantAuthorizationServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.session = self.factory()
        self.memberships = TenantMembershipService(self.session)
        self.user = UserModel(primary_email="member@example.com", status="active")
        self.other_user = UserModel(primary_email="other@example.com", status="active")
        self.session.add_all([self.user, self.other_user])
        self.session.flush()
        self.tenant = self.memberships.create_tenant(name="Tenant", slug="tenant")
        self.other_tenant = self.memberships.create_tenant(name="Other", slug="other")
        self.membership = self.memberships.add_member(tenant_id=self.tenant.id, user_id=self.user.id)
        self.other_membership = self.memberships.add_member(tenant_id=self.other_tenant.id, user_id=self.other_user.id)
        seed_tenant_rbac(self.session, self.tenant.id)
        seed_tenant_rbac(self.session, self.other_tenant.id)
        self.service = TenantAuthorizationService(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def role(self, key, tenant_id=None):
        return self.session.scalar(select(RoleModel).where(
            RoleModel.tenant_id == (tenant_id or self.tenant.id),
            RoleModel.role_key == key,
        ))

    def assign(self, key):
        return self.service.assign_role(
            tenant_id=self.tenant.id,
            membership_id=self.membership.id,
            role_id=self.role(key).id,
            actor_id="test-actor",
        )

    def test_viewer_permissions(self):
        self.assign("viewer")
        effective = self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(effective.roles, {"viewer"})
        self.assertEqual(effective.permissions, {"assets.read", "search.read"})

    def test_operator_permissions(self):
        self.assign("operator")
        effective = self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertTrue({"assets.read", "search.read", "ai_operations.read", "ai_analysis.run", "ai_jobs.retry", "ai_jobs.cancel"} <= effective.permissions)
        self.assertFalse(self.service.has_permission(tenant_id=self.tenant.id, user_id=self.user.id, permission_key="ai_provider.configure"))

    def test_tenant_admin_permissions_exclude_platform_administration(self):
        self.assign("tenant_admin")
        effective = self.service.require_permission(
            tenant_id=self.tenant.id,
            user_id=self.user.id,
            permission_key="tenant_roles.manage",
        )
        self.assertEqual(effective.permissions, set(PERMISSION_DEFINITIONS))
        self.assertNotIn("platform_admin", effective.roles)
        self.assertNotIn("platform.admin", effective.permissions)

    def test_billing_admin_permissions(self):
        self.assign("billing_admin")
        effective = self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(effective.permissions, {"ai_operations.read", "ai_budget.read", "ai_budget.update"})

    def test_multiple_roles_combine_and_removal_removes_access(self):
        self.assign("viewer")
        operator = self.role("operator")
        self.service.assign_role(tenant_id=self.tenant.id, membership_id=self.membership.id, role_id=operator.id)
        effective = self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(effective.roles, {"viewer", "operator"})
        self.assertIn("ai_analysis.run", effective.permissions)
        self.assertTrue(self.service.remove_role(tenant_id=self.tenant.id, membership_id=self.membership.id, role_id=operator.id))
        self.assertNotIn("ai_analysis.run", self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id).permissions)

    def test_custom_role(self):
        role = self.service.create_custom_role(
            tenant_id=self.tenant.id,
            role_key="asset_curator",
            name="Asset curator",
            permission_keys={"assets.read", "assets.manage"},
        )
        self.service.assign_role(tenant_id=self.tenant.id, membership_id=self.membership.id, role_id=role.id)
        effective = self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(effective.permissions, {"assets.read", "assets.manage"})
        self.assertTrue(self.service.delete_role(tenant_id=self.tenant.id, role_id=role.id))

    def test_cross_tenant_assignment_rejected(self):
        with self.assertRaisesRegex(AuthorizationError, "belong to tenant"):
            self.service.assign_role(
                tenant_id=self.tenant.id,
                membership_id=self.membership.id,
                role_id=self.role("viewer", self.other_tenant.id).id,
            )

    def test_inactive_membership_has_no_permissions(self):
        self.assign("viewer")
        self.membership.status = "suspended"
        effective = self.service.get_effective_permissions(tenant_id=self.tenant.id, user_id=self.user.id)
        self.assertEqual(effective.permissions, frozenset())
        with self.assertRaises(AuthorizationError) as error:
            self.service.require_permission(tenant_id=self.tenant.id, user_id=self.user.id, permission_key="assets.read")
        self.assertEqual(error.exception.code, "permission_required")

    def test_protected_system_role_cannot_be_deleted(self):
        with self.assertRaises(AuthorizationError) as error:
            self.service.delete_role(tenant_id=self.tenant.id, role_id=self.role("viewer").id)
        self.assertEqual(error.exception.code, "protected_role")

    def test_duplicate_role_assignment_and_seed_are_idempotent(self):
        first = self.assign("viewer")
        second = self.assign("viewer")
        self.assertEqual(first.id, second.id)
        first_seed = seed_tenant_rbac(self.session, self.tenant.id)
        second_seed = seed_tenant_rbac(self.session, self.tenant.id)
        self.assertEqual(first_seed["permissions_created"], 0)
        self.assertEqual(second_seed, {"permissions_created": 0, "roles_created": 0, "role_permissions_created": 0})
        self.assertEqual(self.session.scalar(select(func.count()).select_from(PermissionModel)), len(PERMISSION_DEFINITIONS))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(RoleModel)), 8)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(MembershipRoleModel)), 1)


if __name__ == "__main__":
    unittest.main()
