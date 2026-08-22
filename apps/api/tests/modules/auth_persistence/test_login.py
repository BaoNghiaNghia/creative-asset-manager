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
from app.modules.authorization.model import MembershipRoleModel, RoleModel
from app.modules.authorization.seed import seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService


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
        seed_tenant_rbac(self.session, self.tenant.id)
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

    def test_existing_identity_login_does_not_require_self_signup(self):
        first = self.resolve("google", "existing-subject", "user@example.com")
        self.session.commit()
        existing = self.resolve(
            "google",
            "existing-subject",
            "updated@example.com",
            Settings(
                AUTH_SELF_SIGNUP_ENABLED=False,
                AUTH_DEFAULT_TENANT_ID=self.tenant.id,
            ),
        )
        self.session.commit()
        self.assertFalse(existing.first_login)
        self.assertEqual(existing.user.id, first.user.id)
        creation_actions = self.session.scalars(
            select(AuthAuditEventModel.action).where(
                AuthAuditEventModel.action == "application_user_created"
            )
        ).all()
        self.assertEqual(creation_actions, ["application_user_created"])

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

    def test_preprovisioned_active_member_can_link_verified_google_identity(self):
        user = UserModel(
            primary_email="viewer@example.com",
            display_name=None,
            avatar_url=None,
            status="active",
        )
        self.session.add(user)
        self.session.flush()
        membership = self.memberships.add_member(
            tenant_id=self.tenant.id,
            user_id=user.id,
            status="active",
        )
        viewer_role = self.session.scalar(
            select(RoleModel).where(
                RoleModel.tenant_id == self.tenant.id,
                RoleModel.role_key == "viewer",
            )
        )
        TenantAuthorizationService(self.session).assign_role(
            tenant_id=self.tenant.id,
            membership_id=membership.id,
            role_id=viewer_role.id,
            reason="Pre-provision Viewer",
        )
        self.session.commit()

        login = ApplicationLoginService(
            self.session,
            Settings(
                AUTH_SELF_SIGNUP_ENABLED=False,
                AUTH_DEFAULT_TENANT_ID=self.tenant.id,
            ),
        ).resolve(
            provider="google",
            provider_subject="viewer-google-subject",
            provider_email="viewer@example.com",
            display_name="Viewer",
            provider_metadata={"email_verified": True},
        )
        self.session.commit()

        self.assertTrue(login.first_login)
        self.assertEqual(login.user.id, user.id)
        self.assertEqual(login.active_tenant_id, self.tenant.id)
        self.assertEqual(
            self.session.scalar(
                select(UserIdentityModel).where(
                    UserIdentityModel.provider_subject == "viewer-google-subject"
                )
            ).user_id,
            user.id,
        )

    def test_allowed_and_denied_email_domains(self):
        allowed_settings = self.settings(
            AUTH_ALLOWED_EMAIL_DOMAINS="example.com, studio.test"
        )
        for provider in ("google", "microsoft"):
            accepted = self.resolve(
                provider,
                f"{provider}-allowed",
                "artist@studio.test",
                allowed_settings,
            )
            self.assertEqual(accepted.active_tenant_id, self.tenant.id)
            with self.assertRaises(LoginAdmissionError) as captured:
                self.resolve(
                    provider,
                    f"{provider}-denied",
                    "artist@outside.test",
                    allowed_settings,
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

    def test_configured_tenant_member_role_is_assigned(self):
        role = RoleModel(
            tenant_id=self.tenant.id,
            role_key="tenant_member",
            name="Tenant member",
            description="Least privilege member",
            status="active",
        )
        self.session.add(role)
        self.session.commit()
        login = self.resolve(
            "microsoft",
            "member-role",
            "member@example.com",
            self.settings(AUTH_SELF_SIGNUP_DEFAULT_ROLE="tenant_member"),
        )
        self.session.commit()
        assigned = self.session.scalar(
            select(RoleModel)
            .join(MembershipRoleModel, MembershipRoleModel.role_id == RoleModel.id)
            .join(
                TenantMembershipModel,
                TenantMembershipModel.id
                == MembershipRoleModel.tenant_membership_id,
            )
            .where(
                TenantMembershipModel.user_id == login.user.id,
                RoleModel.role_key == "tenant_member",
            )
        )
        self.assertEqual(assigned.id, role.id)

    def test_development_personal_tenant_seeds_default_role(self):
        login = self.resolve(
            "google",
            "development-personal",
            "developer@example.com",
            Settings(
                APP_ENV="development",
                AUTH_SELF_SIGNUP_ENABLED=True,
                AUTH_DEFAULT_TENANT_ID="",
                AUTH_SELF_SIGNUP_DEFAULT_ROLE="viewer",
                DEVELOPMENT_PERSONAL_TENANT_ENABLED=True,
            ),
        )
        self.session.commit()
        assigned = self.session.scalar(
            select(RoleModel)
            .join(MembershipRoleModel, MembershipRoleModel.role_id == RoleModel.id)
            .where(RoleModel.tenant_id == login.active_tenant_id)
        )
        self.assertEqual(assigned.role_key, "viewer")

    def test_first_login_assigns_least_privilege_role_and_audits(self):
        login = self.resolve("google", "plain", "plain@example.com")
        self.session.commit()
        membership = self.session.scalar(
            select(TenantMembershipModel).where(
                TenantMembershipModel.user_id == login.user.id,
                TenantMembershipModel.tenant_id == self.tenant.id,
            )
        )
        self.assertEqual(membership.status, "active")
        assigned_role = self.session.scalar(
            select(RoleModel)
            .join(MembershipRoleModel, MembershipRoleModel.role_id == RoleModel.id)
            .where(
                MembershipRoleModel.tenant_membership_id == membership.id,
            )
        )
        self.assertEqual(assigned_role.role_key, "viewer")
        self.assertNotIn(
            assigned_role.role_key, {"tenant_admin", "platform_admin"}
        )
        actions = set(
            self.session.scalars(select(AuthAuditEventModel.action))
        )
        self.assertTrue(
            {
                "application_login",
                "application_user_registered",
                "application_user_created",
                "provider_identity_created",
                "tenant_membership_created",
                "self_signup_default_role_assigned",
            }
            <= actions
        )
        role_audit = self.session.scalar(
            select(AuthAuditEventModel).where(
                AuthAuditEventModel.action
                == "self_signup_default_role_assigned"
            )
        )
        self.assertEqual(role_audit.provider, "google")
        self.assertEqual(role_audit.tenant_id, self.tenant.id)
        self.assertEqual(role_audit.detail_json["role_key"], "viewer")

    def test_missing_default_tenant_rolls_back_all_provisioning(self):
        with self.assertRaises(LoginAdmissionError) as captured:
            self.resolve(
                "microsoft",
                "missing-tenant",
                "missing@example.com",
                Settings(
                    AUTH_SELF_SIGNUP_ENABLED=True,
                    AUTH_DEFAULT_TENANT_ID="missing",
                ),
            )
        self.assertEqual(captured.exception.code, "default_tenant_unavailable")
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(UserModel)), 0
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(UserIdentityModel)
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
