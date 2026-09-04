import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.auth_persistence.identity import IdentityResolutionService
from app.modules.auth_persistence.model import (
    AuthAuditEventModel,
    AuthSessionModel,
    OAuthConnectionModel,
    TenantMembershipModel,
    TenantModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.auth_persistence.repository import AuthPersistenceRepository
from app.modules.authorization.model import (
    MembershipRoleModel,
    PlatformAdminAssignmentModel,
    RoleModel,
)
from app.operations import auth_cli


class AuthMigrationOperationsTest(unittest.TestCase):
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

    def tearDown(self):
        self.engine.dispose()

    def create_identity(self, provider="google", subject="stable-subject"):
        with self.factory() as session:
            user, _identity = IdentityResolutionService(session).resolve_login(
                provider=provider,
                provider_subject=subject,
                provider_email="owner@example.com",
            )
            session.commit()
            return user.id

    def test_bootstrap_is_confirmed_idempotent_and_platform_admin_is_separate(self):
        user_id = self.create_identity()
        with patch("app.operations.auth_cli.SessionLocal", self.factory):
            with self.assertRaisesRegex(ValueError, "--confirm"):
                auth_cli.bootstrap_access(
                    provider="google",
                    subject="stable-subject",
                    tenant_id=None,
                    tenant_name="Studio",
                    tenant_slug="studio",
                    reason="initialize access",
                    dry_run=False,
                    confirmed=False,
                )
            preview = auth_cli.bootstrap_access(
                provider="google",
                subject="stable-subject",
                tenant_id=None,
                tenant_name="Studio",
                tenant_slug="studio",
                reason="preview",
                dry_run=True,
                confirmed=False,
            )
            self.assertTrue(preview["dry_run"])
            with self.factory() as session:
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(TenantModel)), 0
                )
            first = auth_cli.bootstrap_access(
                provider="google",
                subject="stable-subject",
                tenant_id=None,
                tenant_name="Studio",
                tenant_slug="studio",
                reason="approved",
                dry_run=False,
                confirmed=True,
            )
            second = auth_cli.bootstrap_access(
                provider="google",
                subject="stable-subject",
                tenant_id=first["tenant_id"],
                tenant_name=None,
                tenant_slug=None,
                reason="idempotency",
                dry_run=False,
                confirmed=True,
            )
            self.assertEqual(first["tenant_id"], second["tenant_id"])
            self.assertFalse(second["membership_created"])

            with self.factory() as session:
                role = session.scalar(
                    select(RoleModel).where(RoleModel.role_key == "tenant_admin")
                )
                self.assertIsNotNone(role)
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(TenantMembershipModel)
                    ),
                    1,
                )
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(MembershipRoleModel)
                    ),
                    1,
                )
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(
                            PlatformAdminAssignmentModel
                        )
                    ),
                    0,
                )

            platform_preview = auth_cli.grant_platform_admin(
                provider="google",
                subject="stable-subject",
                granted_by_user_id=user_id,
                reason="preview platform bootstrap",
                dry_run=True,
                confirmed=False,
            )
            self.assertTrue(platform_preview["dry_run"])
            with self.factory() as session:
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(
                            PlatformAdminAssignmentModel
                        )
                    ),
                    0,
                )

            granted = auth_cli.grant_platform_admin(
                provider="google",
                subject="stable-subject",
                granted_by_user_id=user_id,
                reason="separate platform bootstrap",
                dry_run=False,
                confirmed=True,
            )
            self.assertEqual(granted["user_id"], user_id)
            with self.factory() as session:
                self.assertEqual(
                    session.scalar(
                        select(func.count()).select_from(
                            PlatformAdminAssignmentModel
                        )
                    ),
                    1,
                )
                serialized = str(
                    [row.detail_json for row in session.scalars(select(AuthAuditEventModel))]
                ).casefold()
                self.assertNotIn("token", serialized)
                self.assertNotIn("stable-subject", serialized)

    def test_backfill_is_paginated_idempotent_and_does_not_email_link(self):
        cipher = TokenCipher({"v1": b"1" * 32}, "v1")
        with self.factory() as session:
            tenant = TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a")
            session.add(tenant)
            session.flush()
            repository = AuthPersistenceRepository(session, cipher)
            for provider, subject in (
                ("google", "google-subject"),
                ("microsoft", "microsoft-subject"),
            ):
                connection = repository.upsert_connection(
                    tenant_id=tenant.id,
                    provider=provider,
                    provider_account_id=subject,
                    connection_purpose="application_login",
                    account_email="same@example.com",
                    access_token="access-secret",
                    refresh_token="refresh-secret",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    scopes=[],
                    token_type="Bearer",
                )
                repository.create_session(
                    connection=connection,
                    user={"id": subject, "email": "same@example.com"},
                    ttl_seconds=3600,
                )
            session.commit()

        with patch("app.operations.auth_cli.SessionLocal", self.factory):
            preview = auth_cli.backfill_legacy_auth(
                page_size=1,
                after_id=None,
                max_pages=None,
                actor_id=None,
                reason="AUTH-09 preview",
                dry_run=True,
                confirmed=False,
            )
            self.assertEqual(preview["processed"], 2)
            with self.factory() as session:
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(UserModel)), 0
                )
                self.assertTrue(
                    all(row.user_id is None for row in session.scalars(select(AuthSessionModel)))
                )
            first = auth_cli.backfill_legacy_auth(
                page_size=1,
                after_id=None,
                max_pages=None,
                actor_id=None,
                reason="AUTH-09 migration",
                dry_run=False,
                confirmed=True,
            )
            self.assertEqual(first["processed"], 2)
            self.assertEqual(first["identities_created"], 2)
            self.assertEqual(first["sessions_updated"], 2)
            second = auth_cli.backfill_legacy_auth(
                page_size=1,
                after_id=None,
                max_pages=None,
                actor_id=None,
                reason="AUTH-09 idempotency",
                dry_run=False,
                confirmed=True,
            )
            self.assertEqual(second["identities_created"], 0)
            self.assertEqual(second["memberships_created"], 0)
            self.assertEqual(second["sessions_updated"], 0)

        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(UserModel)), 2
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(UserIdentityModel)), 2
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(TenantMembershipModel)),
                2,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(AuthSessionModel).where(
                        AuthSessionModel.user_id.is_not(None),
                        AuthSessionModel.active_tenant_id == "tenant-a",
                    )
                ),
                2,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(PlatformAdminAssignmentModel)
                ),
                0,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MembershipRoleModel)),
                0,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(OAuthConnectionModel)),
                2,
            )

    def test_setup_identity_listing_is_safe_and_access_verification_is_exact(self):
        self.create_identity(subject="long-provider-subject-secret")
        with patch("app.operations.auth_cli.SessionLocal", self.factory):
            listed = auth_cli.list_identity_candidates(
                provider="google", subject="long-provider-subject-secret"
            )
            self.assertEqual(len(listed["identities"]), 1)
            summary = listed["identities"][0]
            self.assertEqual(summary["masked_email"], "o***@example.com")
            self.assertNotIn("long-provider-subject-secret", str(listed))
            reference = auth_cli.resolve_identity_reference(
                identity_id=summary["identity_id"]
            )
            self.assertEqual(reference["subject"], "long-provider-subject-secret")
            bootstrapped = auth_cli.bootstrap_access(
                provider="google",
                subject=reference["subject"],
                tenant_id=None,
                tenant_name="Studio",
                tenant_slug="studio",
                reason="setup script regression",
                dry_run=False,
                confirmed=True,
            )
            verified = auth_cli.verify_bootstrap_access(
                provider="google",
                subject=reference["subject"],
                tenant_id=bootstrapped["tenant_id"],
                expect_platform_admin=False,
            )
            self.assertIn("tenant_admin", verified["roles"])
            self.assertEqual(
                set(verified["permissions_verified"]),
                {"ai_operations.read", "tenant_members.manage"},
            )
            with self.assertRaisesRegex(RuntimeError, "platform administrator"):
                auth_cli.verify_bootstrap_access(
                    provider="google",
                    subject=reference["subject"],
                    tenant_id=bootstrapped["tenant_id"],
                    expect_platform_admin=True,
                )


if __name__ == "__main__":
    unittest.main()
