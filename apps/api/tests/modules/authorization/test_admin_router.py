import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import AuthAuditEventModel, UserModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.admin_router import router
from app.modules.authorization.model import RoleModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.authorization.seed import PERMISSION_DEFINITIONS, seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService


class TenantAccessAdminRouterTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            memberships = TenantMembershipService(session)
            self.tenant = memberships.create_tenant(name="Studio", slug="studio")
            self.other_tenant = memberships.create_tenant(name="Other", slug="other")
            self.admin = UserModel(primary_email="admin@example.com", display_name="Admin", status="active")
            self.member = UserModel(primary_email="member@example.com", display_name="Member", avatar_url="https://lh3.googleusercontent.com/member-avatar", status="active")
            session.add_all([self.admin, self.member])
            session.flush()
            self.admin_membership = memberships.add_member(tenant_id=self.tenant.id, user_id=self.admin.id)
            seed_tenant_rbac(session, self.tenant.id)
            seed_tenant_rbac(session, self.other_tenant.id)
            admin_role = session.scalar(select(RoleModel).where(RoleModel.tenant_id == self.tenant.id, RoleModel.role_key == "tenant_admin"))
            TenantAuthorizationService(session).assign_role(
                tenant_id=self.tenant.id,
                membership_id=self.admin_membership.id,
                role_id=admin_role.id,
                actor_id="bootstrap",
            )
            session.commit()
            self.admin_role_id = admin_role.id
        self.principal = CurrentPrincipal(
            user_id=self.admin.id,
            active_tenant_id=self.tenant.id,
            membership_id=self.admin_membership.id,
            external_identity=None,
            effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset(PERMISSION_DEFINITIONS),
            platform_admin=False,
            session_id="safe-hash",
            authorization_source="tenant_rbac",
        )
        self.app = FastAPI()
        self.app.include_router(router)
        for route in self.app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            for dependency in dependant.dependencies:
                self.app.dependency_overrides[dependency.call] = lambda: self.principal
        self.client = TestClient(self.app)

    def tearDown(self):
        self.engine.dispose()

    def request(self, method, path, **kwargs):
        with patch("app.modules.authorization.admin_router.SessionLocal", self.factory):
            return self.client.request(method, path, **kwargs)

    def test_member_invite_list_filters_roles_and_permissions(self):
        response = self.request(
            "POST",
            f"/api/v1/tenants/{self.tenant.id}/members",
            json={"email": "member@example.com", "status": "invited", "reason": "approved invitation"},
        )
        self.assertEqual(response.status_code, 201)
        membership_id = response.json()["membership_id"]
        listed = self.request(
            "GET",
            f"/api/v1/tenants/{self.tenant.id}/members?status=invited&query=member&page=1&page_size=10",
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1)
        self.assertEqual(listed.json()["items"][0]["avatar_url"], "https://lh3.googleusercontent.com/member-avatar")
        self.assertEqual(listed.json()["items"][0]["membership_id"], membership_id)
        roles = self.request("GET", f"/api/v1/tenants/{self.tenant.id}/roles")
        permissions = self.request("GET", f"/api/v1/tenants/{self.tenant.id}/permissions")
        self.assertEqual(roles.status_code, 200)
        self.assertEqual(roles.json()["total"], 4)
        self.assertEqual(len(permissions.json()["items"]), len(PERMISSION_DEFINITIONS))
        serialized = (listed.text + roles.text + permissions.text).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("session", serialized)

    def test_membership_lifecycle_duplicate_and_audit(self):
        created = self.request(
            "POST",
            f"/api/v1/tenants/{self.tenant.id}/members",
            json={"user_id": self.member.id, "status": "active", "reason": "add member"},
        )
        membership_id = created.json()["membership_id"]
        duplicate = self.request(
            "POST",
            f"/api/v1/tenants/{self.tenant.id}/members",
            json={"user_id": self.member.id, "status": "active", "reason": "duplicate member"},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"]["code"], "membership_exists")
        for action, expected in (("suspend", "suspended"), ("restore", "active"), ("remove", "removed")):
            changed = self.request(
                "PATCH",
                f"/api/v1/tenants/{self.tenant.id}/members/{membership_id}",
                json={"action": action, "reason": f"approved {action}"},
            )
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(changed.json()["status"], expected)
        with self.factory() as session:
            actions = set(session.scalars(select(AuthAuditEventModel.action).where(AuthAuditEventModel.tenant_id == self.tenant.id)))
        self.assertTrue({"tenant_member_added", "tenant_member_suspend", "tenant_member_restore", "tenant_member_remove"} <= actions)

    def test_role_assignment_custom_role_and_protected_role(self):
        member = self.request(
            "POST",
            f"/api/v1/tenants/{self.tenant.id}/members",
            json={"user_id": self.member.id, "status": "active", "reason": "add member"},
        ).json()
        role = self.request(
            "POST",
            f"/api/v1/tenants/{self.tenant.id}/roles",
            json={"role_key": "reviewer", "name": "Reviewer", "permission_keys": ["assets.read"], "reason": "create reviewer"},
        )
        self.assertEqual(role.status_code, 201)
        role_id = role.json()["id"]
        assigned = self.request(
            "POST",
            f"/api/v1/tenants/{self.tenant.id}/members/{member['membership_id']}/roles",
            json={"role_id": role_id, "reason": "assign reviewer"},
        )
        self.assertEqual(assigned.status_code, 201)
        removed = self.request(
            "DELETE",
            f"/api/v1/tenants/{self.tenant.id}/members/{member['membership_id']}/roles/{role_id}",
            json={"reason": "remove reviewer"},
        )
        self.assertTrue(removed.json()["removed"])
        protected = self.request(
            "DELETE",
            f"/api/v1/tenants/{self.tenant.id}/roles/{self.admin_role_id}",
            json={"reason": "unsafe deletion"},
        )
        self.assertEqual(protected.status_code, 409)
        self.assertEqual(protected.json()["detail"]["code"], "protected_role")

    def test_final_admin_cross_tenant_and_permission_denial(self):
        final_admin = self.request(
            "PATCH",
            f"/api/v1/tenants/{self.tenant.id}/members/{self.admin_membership.id}",
            json={"action": "remove", "reason": "unsafe removal"},
        )
        self.assertEqual(final_admin.status_code, 409)
        self.assertEqual(final_admin.json()["detail"]["code"], "final_tenant_admin")
        cross_tenant = self.request("GET", f"/api/v1/tenants/{self.other_tenant.id}/members")
        self.assertEqual(cross_tenant.status_code, 403)
        self.assertEqual(cross_tenant.json()["detail"]["code"], "tenant_mismatch")

        members_route = next(
            route for route in self.app.routes
            if getattr(route, "path", "") == "/api/v1/tenants/{tenant_id}/members" and "GET" in route.methods
        )
        dependency = members_route.dependant.dependencies[0].call
        denied = CurrentPrincipal(
            user_id=self.member.id,
            active_tenant_id=self.tenant.id,
            membership_id="member",
            external_identity=None,
            effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"assets.read"}),
            platform_admin=False,
            session_id="safe",
            authorization_source="tenant_rbac",
        )
        with self.assertRaises(HTTPException) as captured:
            dependency(denied)
        self.assertEqual(captured.exception.status_code, 403)
        self.assertEqual(captured.exception.detail["code"], "permission_required")


if __name__ == "__main__":
    unittest.main()
