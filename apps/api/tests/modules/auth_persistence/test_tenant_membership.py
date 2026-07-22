import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.model import (
    AuthSessionModel,
    AuthAuditEventModel,
    TenantMembershipModel,
    TenantModel,
    UserModel,
)
from app.modules.auth_persistence.tenant_membership import (
    TenantAccessError,
    TenantMembershipService,
)


class TenantMembershipServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def user(session, email):
        row = UserModel(primary_email=email, display_name=email, status="active")
        session.add(row)
        session.flush()
        return row

    def test_one_user_in_one_and_multiple_tenants(self):
        with self.factory() as session:
            user = self.user(session, "one@example.com")
            service = TenantMembershipService(session)
            first = service.create_tenant(name="First", slug="first")
            second = service.create_tenant(name="Second", slug="second")
            service.add_member(tenant_id=first.id, user_id=user.id)
            self.assertEqual([item[1].id for item in service.list_user_tenants(user.id)], [first.id])
            service.add_member(tenant_id=second.id, user_id=user.id)
            self.assertEqual({item[1].id for item in service.list_user_tenants(user.id)}, {first.id, second.id})

    def test_multiple_users_share_one_tenant(self):
        with self.factory() as session:
            first = self.user(session, "first@example.com")
            second = self.user(session, "second@example.com")
            service = TenantMembershipService(session)
            tenant = service.create_tenant(name="Shared", slug="shared")
            service.add_member(tenant_id=tenant.id, user_id=first.id)
            service.add_member(tenant_id=tenant.id, user_id=second.id)
            count = session.scalar(select(func.count()).select_from(TenantMembershipModel))
            self.assertEqual(count, 2)

    def test_inactive_membership_and_suspended_tenant_are_rejected(self):
        with self.factory() as session:
            user = self.user(session, "inactive@example.com")
            service = TenantMembershipService(session)
            tenant = service.create_tenant(name="Tenant", slug="tenant")
            membership = service.add_member(tenant_id=tenant.id, user_id=user.id)
            membership.status = "suspended"
            with self.assertRaisesRegex(TenantAccessError, "active tenant membership"):
                service.require_active_membership(tenant.id, user.id)
            membership.status = "active"
            tenant.status = "suspended"
            with self.assertRaisesRegex(TenantAccessError, "tenant is not active"):
                service.require_active_membership(tenant.id, user.id)

    def test_invalid_active_tenant_and_cross_tenant_access(self):
        with self.factory() as session:
            owner = self.user(session, "owner@example.com")
            outsider = self.user(session, "outsider@example.com")
            service = TenantMembershipService(session)
            tenant = service.create_tenant(name="Private", slug="private")
            service.add_member(tenant_id=tenant.id, user_id=owner.id)
            auth_session = AuthSessionModel(
                session_id_hash="session-hash",
                user_id=owner.id,
                tenant_id="legacy-provider-tenant",
                provider="google",
                connection_id="connection-id",
                user_json={"id": "provider-subject"},
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            session.add(auth_session)
            session.flush()
            context = service.select_active_tenant(
                session_id_hash="session-hash",
                user_id=owner.id,
                tenant_id=tenant.id,
            )
            self.assertEqual(context.tenant_id, tenant.id)
            self.assertEqual(session.scalar(select(func.count()).select_from(AuthAuditEventModel)), 1)
            with self.assertRaises(TenantAccessError):
                service.select_active_tenant(
                    session_id_hash="session-hash",
                    user_id=outsider.id,
                    tenant_id=tenant.id,
                )
            with self.assertRaises(TenantAccessError):
                service.require_active_membership(tenant.id, outsider.id)

    def test_duplicate_membership_is_idempotent_and_removal_preserves_history(self):
        with self.factory() as session:
            user = self.user(session, "member@example.com")
            service = TenantMembershipService(session)
            tenant = service.create_tenant(name="Tenant", slug="history")
            first = service.add_member(tenant_id=tenant.id, user_id=user.id)
            duplicate = service.add_member(tenant_id=tenant.id, user_id=user.id)
            self.assertEqual(first.id, duplicate.id)
            service.remove_member(tenant.id, user.id)
            session.commit()
            preserved = service.get_membership(tenant.id, user.id)
            self.assertEqual(preserved.status, "removed")
            self.assertEqual(session.scalar(select(func.count()).select_from(TenantMembershipModel)), 1)
            service.restore_member(tenant.id, user.id)
            self.assertEqual(preserved.status, "active")

    def test_active_tenant_resolution_and_legacy_compatibility(self):
        with self.factory() as session:
            service = TenantMembershipService(session)
            legacy = SimpleNamespace(user_id=None, tenant_id="legacy", active_tenant_id=None)
            context = service.resolve_active_tenant(legacy)
            self.assertTrue(context.legacy)
            self.assertEqual(context.tenant_id, "legacy")

            user = self.user(session, "resolved@example.com")
            tenant = service.create_tenant(name="Resolved", slug="resolved")
            membership = service.add_member(tenant_id=tenant.id, user_id=user.id)
            cloud = SimpleNamespace(
                user_id=user.id,
                tenant_id="provider-account",
                active_tenant_id=tenant.id,
                session_id_hash="not-required-for-explicit-selection",
            )
            context = service.resolve_active_tenant(cloud)
            self.assertEqual(context.tenant_id, tenant.id)
            self.assertEqual(context.membership_id, membership.id)

    def test_development_personal_tenant_is_explicit_and_idempotent(self):
        with self.factory() as session:
            user = self.user(session, "dev@example.com")
            service = TenantMembershipService(session)
            disabled = Settings(DEVELOPMENT_PERSONAL_TENANT_ENABLED=False, APP_ENV="local")
            self.assertIsNone(service.ensure_development_personal_tenant(
                settings=disabled, user=user, legacy_tenant_id="provider-id", display_name="Dev"
            ))
            enabled = Settings(DEVELOPMENT_PERSONAL_TENANT_ENABLED=True, APP_ENV="local")
            first = service.ensure_development_personal_tenant(
                settings=enabled, user=user, legacy_tenant_id="provider-id", display_name="Dev"
            )
            second = service.ensure_development_personal_tenant(
                settings=enabled, user=user, legacy_tenant_id="provider-id", display_name="Dev"
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual(session.scalar(select(func.count()).select_from(TenantModel)), 1)


if __name__ == "__main__":
    unittest.main()
