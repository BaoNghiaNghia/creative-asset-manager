import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.model import PermissionModel, RoleModel
from app.modules.authorization.seed import PERMISSION_DEFINITIONS
from app.operations.auth_cli import seed_rbac


class TenantRbacSeedCommandTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            tenant = TenantMembershipService(session).create_tenant(name="Seed", slug="seed")
            session.commit()
            self.tenant_id = tenant.id

    def tearDown(self):
        self.engine.dispose()

    def test_seed_requires_confirmation_supports_dry_run_and_is_idempotent(self):
        with patch("app.operations.auth_cli.SessionLocal", self.factory):
            with self.assertRaisesRegex(ValueError, "--confirm"):
                seed_rbac(tenant_id=self.tenant_id, reason="no confirmation", dry_run=False, confirmed=False)
            preview = seed_rbac(tenant_id=self.tenant_id, reason="preview", dry_run=True, confirmed=False)
            self.assertTrue(preview["dry_run"])
            with self.factory() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(RoleModel)), 0)

            first = seed_rbac(tenant_id=self.tenant_id, reason="approved", dry_run=False, confirmed=True)
            second = seed_rbac(tenant_id=self.tenant_id, reason="repeat", dry_run=False, confirmed=True)
            self.assertEqual(first["roles_created"], 4)
            self.assertEqual(second["roles_created"], 0)
            with self.factory() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(PermissionModel)), len(PERMISSION_DEFINITIONS))
                roles = session.scalars(select(RoleModel)).all()
                self.assertEqual({role.role_key for role in roles}, {"viewer", "operator", "tenant_admin", "billing_admin"})
                self.assertTrue(all(role.protected and role.is_system for role in roles))
                self.assertNotIn("platform_admin", {role.role_key for role in roles})
                self.assertEqual(session.scalar(select(func.count()).select_from(AuthAuditEventModel)), 2)


if __name__ == "__main__":
    unittest.main()
