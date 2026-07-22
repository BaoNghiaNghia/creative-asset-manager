import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.login import (
    ApplicationLoginService,
    LoginAdmissionError,
)
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    TenantMembershipModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.model import MembershipRoleModel


class ApplicationLoginServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        self.session = self.factory()
        self.memberships = TenantMembershipService(self.session)
        self.tenant = self.memberships.create_tenant(
            tenant_id="tenant-default", name="Default", slug="default"
        )
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def settings(self, **overrides):
        return Settings(
            AUTH_SELF_SIGNUP_ENABLED=True,
            AUTH_DEFAULT_TENANT_ID=self.tenant.id,
            **overrides,
        )

    def resolve(self, provider, subject, email, settings=None):
        return ApplicationLoginService(
            self.session, settings or self.settings()
        ).resolve(
            provider=provider,
            provider_subject=subject,
            provider_email=email,
            display_name=f"{provider.title()} User",
            avatar_url="https://images.example/avatar.png",
            provider_metadata={"locale": "en"} if provider == "google" else {},
        )

    def test_first_and_repeat_google_login_reuse_identity(self):
        first = self.resolve("google", "google-subject", "USER@example.com")
        self.session.commit()
        second = self.resolve("google", "google-subject", "changed@example.com")
        self.session.commit()
        self.assertTrue(first.first_login)
        self.assertFalse(second.first_login)
        self.assertEqual(first.user.id, second.user.id)
        self.assertEqual(first.identity.id, second.identity.id)
        self.assertEqual(second.active_tenant_id, self.tenant.id)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(UserModel)), 1
        )

    def test_first_and_repeat_microsoft_login_reuse_identity(self):
        first = self.resolve("microsoft", "microsoft-subject", "ms@example.com")
        self.session.commit()
        second = self.resolve("microsoft", "microsoft-subject", "ms@example.com")
        self.session.commit()
        self.assertTrue(first.first_login)
        self.assertFalse(second.first_login)
        self.assertEqual(first.user.id, second.user.id)
        self.assertEqual(first.active_tenant_id, self.tenant.id)

    def test_self_signup_disabled(self):
        settings = Settings(
            AUTH_SELF_SIGNUP_ENABLED=False,
            AUTH_DEFAULT_TENANT_ID=self.tenant.id,
        )
        with self.assertRaises(LoginAdmissionError) as captured:
            self.resolve("google", "new-subject", "new@example.com", settings)
        self.assertEqual(captured.exception.code, "self_signup_disabled")
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(UserModel)), 0
        )

    def test_allowed_and_denied_email_domains(self):
        allowed_settings = self.settings(
            AUTH_ALLOWED_EMAIL_DOMAINS="example.com, studio.test"
        )
        accepted = self.resolve(
            "google", "allowed", "artist@studio.test", allowed_settings
        )
        self.assertEqual(accepted.active_tenant_id, self.tenant.id)
        with self.assertRaises(LoginAdmissionError) as captured:
            self.resolve(
                "google", "denied", "artist@outside.test", allowed_settings
            )
        self.assertEqual(captured.exception.code, "email_domain_not_allowed")

    def test_google_and_microsoft_same_email_are_not_linked(self):
        google = self.resolve("google", "google-one", "same@example.com")
        microsoft = self.resolve(
            "microsoft", "microsoft-one", "same@example.com"
        )
        self.session.commit()
        self.assertNotEqual(google.user.id, microsoft.user.id)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(UserIdentityModel)),
            2,
        )

    def test_single_and_multiple_tenant_selection(self):
        login = self.resolve("google", "multi", "multi@example.com")
        other = self.memberships.create_tenant(
            tenant_id="tenant-other", name="Other", slug="other"
        )
        self.memberships.add_member(tenant_id=other.id, user_id=login.user.id)
        self.session.commit()
        selected = self.resolve(
            "google",
            "multi",
            "multi@example.com",
            Settings(
                AUTH_SELF_SIGNUP_ENABLED=False,
                AUTH_DEFAULT_TENANT_ID=other.id,
            ),
        )
        self.assertEqual(login.active_tenant_id, self.tenant.id)
        self.assertEqual(selected.active_tenant_id, other.id)

    def test_disabled_user_rejected(self):
        login = self.resolve("google", "disabled", "disabled@example.com")
        login.user.status = "disabled"
        self.session.commit()
        with self.assertRaises(PermissionError):
            self.resolve("google", "disabled", "disabled@example.com")

    def test_first_login_assigns_membership_but_no_admin_role_and_audits(self):
        login = self.resolve("google", "plain", "plain@example.com")
        self.session.commit()
        membership = self.session.scalar(
            select(TenantMembershipModel).where(
                TenantMembershipModel.user_id == login.user.id,
                TenantMembershipModel.tenant_id == self.tenant.id,
            )
        )
        self.assertEqual(membership.status, "active")
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MembershipRoleModel)),
            0,
        )
        actions = set(
            self.session.scalars(select(AuthAuditEventModel.action))
        )
        self.assertTrue(
            {"application_login", "application_user_registered"} <= actions
        )


if __name__ == "__main__":
    unittest.main()
