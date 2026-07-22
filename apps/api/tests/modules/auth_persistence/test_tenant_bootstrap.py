import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import AuthAuditEventModel, TenantModel, UserModel
from app.operations.auth_cli import bootstrap_tenant


class TenantBootstrapCommandTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.factory() as session:
            user = UserModel(primary_email="bootstrap@example.com", status="active")
            session.add(user)
            session.commit()
            self.user_id = user.id

    def tearDown(self):
        self.engine.dispose()

    def test_confirmation_dry_run_and_idempotent_bootstrap(self):
        with patch("app.operations.auth_cli.SessionLocal", self.factory):
            with self.assertRaisesRegex(ValueError, "--confirm"):
                bootstrap_tenant(
                    user_id=self.user_id, name="Tenant", slug="tenant",
                    tenant_id=None, reason="first tenant", dry_run=False,
                    confirmed=False,
                )
            preview = bootstrap_tenant(
                user_id=self.user_id, name="Tenant", slug="tenant",
                tenant_id=None, reason="preview", dry_run=True, confirmed=False,
            )
            self.assertTrue(preview["dry_run"])
            with self.factory() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(TenantModel)), 0)

            first = bootstrap_tenant(
                user_id=self.user_id, name="Tenant", slug="tenant",
                tenant_id=None, reason="approved bootstrap", dry_run=False,
                confirmed=True,
            )
            second = bootstrap_tenant(
                user_id=self.user_id, name="Tenant", slug="tenant",
                tenant_id=first["tenant_id"], reason="idempotency check",
                dry_run=False, confirmed=True,
            )
            self.assertEqual(first["tenant_id"], second["tenant_id"])
            self.assertFalse(second["created"])
            with self.factory() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(TenantModel)), 1)
                audits = session.scalars(select(AuthAuditEventModel)).all()
                self.assertEqual(len(audits), 2)
                self.assertNotIn("token", str([item.detail_json for item in audits]).lower())


if __name__ == "__main__":
    unittest.main()
