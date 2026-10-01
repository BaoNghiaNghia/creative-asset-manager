import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import OAuthConnectionModel, UserModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.principal import CurrentPrincipal, ExternalIdentitySummary
from app.modules.authorization.router import identity


class AuthorizationIdentityAvatarTest(unittest.TestCase):
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
            tenant = memberships.create_tenant(name="Studio", slug="studio")
            user = UserModel(
                primary_email="member@gmail.com",
                display_name="Google Member",
                avatar_url="https://lh3.googleusercontent.com/google-member",
                status="active",
            )
            session.add(user)
            session.flush()
            membership = memberships.add_member(tenant_id=tenant.id, user_id=user.id)
            session.commit()
            self.principal = CurrentPrincipal(
                user_id=user.id,
                active_tenant_id=tenant.id,
                membership_id=membership.id,
                external_identity=None,
                effective_roles=frozenset({"viewer"}),
                effective_permissions=frozenset({"assets.read"}),
                platform_admin=False,
                session_id="safe-hash",
                authorization_source="tenant_rbac",
            )

    def tearDown(self):
        self.engine.dispose()

    def test_identity_exposes_safe_profile_fields_for_the_header(self):
        with patch("app.modules.authorization.router.SessionLocal", self.factory):
            payload = identity(self.principal)
        self.assertEqual(payload["display_name"], "Google Member")
        self.assertEqual(payload["email"], "member@gmail.com")
        self.assertEqual(
            payload["avatar_url"],
            "https://lh3.googleusercontent.com/google-member",
        )
        serialized = str(payload).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("session", serialized)

    def test_identity_falls_back_to_google_connection_picture_for_legacy_user(self):
        google_subject = "google-subject-1"
        with self.factory() as session:
            user = session.get(UserModel, self.principal.user_id)
            user.avatar_url = None
            session.add(OAuthConnectionModel(
                tenant_id=self.principal.active_tenant_id,
                provider="google",
                provider_account_id=google_subject,
                connection_purpose="google_drive_source",
                account_email="member@gmail.com",
                scopes_json=[],
                key_version="test",
                provider_metadata_json={
                    "picture": "https://lh3.googleusercontent.com/google-fallback"
                },
                status="active",
            ))
            session.commit()

        principal = CurrentPrincipal(
            user_id=self.principal.user_id,
            active_tenant_id=self.principal.active_tenant_id,
            membership_id=self.principal.membership_id,
            external_identity=ExternalIdentitySummary(
                provider="google",
                provider_subject=google_subject,
                provider_email="member@gmail.com",
            ),
            effective_roles=self.principal.effective_roles,
            effective_permissions=self.principal.effective_permissions,
            platform_admin=False,
            session_id="safe-hash",
            authorization_source="tenant_rbac",
        )
        with patch("app.modules.authorization.router.SessionLocal", self.factory):
            payload = identity(principal)
        self.assertEqual(
            payload["avatar_url"],
            "https://lh3.googleusercontent.com/google-fallback",
        )


if __name__ == "__main__":
    unittest.main()
