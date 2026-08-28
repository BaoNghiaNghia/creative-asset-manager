import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import UserModel
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.principal import CurrentPrincipal
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


if __name__ == "__main__":
    unittest.main()
