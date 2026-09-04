import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.auth_persistence.identity import (
    ApplicationUserInactiveError,
    IdentityResolutionService,
)
from app.modules.auth_persistence.model import (
    AuthSessionModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.auth_persistence.repository import AuthPersistenceRepository


class ApplicationIdentityTest(unittest.TestCase):
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

    def resolve(self, session, provider, subject, email):
        return IdentityResolutionService(session).resolve_login(
            provider=provider,
            provider_subject=subject,
            provider_email=email,
            display_name="Display Name",
            avatar_url="https://images.example/avatar.png?temporary=value",
            provider_metadata={
                "email_verified": True,
                "user_principal_name": email,
                "access_token": "must-not-persist",
                "refresh_token": "must-not-persist",
                "debug": "x" * 20_000,
            },
        )

    def test_create_google_and_microsoft_users_without_email_linking(self):
        with self.factory() as session:
            google_user, google = self.resolve(
                session, "GOOGLE", "google-subject", " Same@Example.com "
            )
            microsoft_user, microsoft = self.resolve(
                session, "microsoft", "microsoft-subject", "same@example.com"
            )
            session.commit()
            self.assertNotEqual(google_user.id, microsoft_user.id)
            self.assertEqual(google.provider, "google")
            self.assertEqual(microsoft.provider, "microsoft")
            self.assertEqual(google_user.primary_email, "same@example.com")
            self.assertEqual(
                session.scalar(select(func.count()).select_from(UserModel)), 2
            )

    def test_repeat_login_reuses_subject_and_email_change_updates_profile(self):
        with self.factory() as session:
            first_user, first_identity = self.resolve(
                session, "google", "stable-subject", "old@example.com"
            )
            session.commit()
            first_login = first_identity.last_login_at
            second_user, second_identity = self.resolve(
                session, "google", "stable-subject", "new@example.com"
            )
            session.commit()
            self.assertEqual(first_user.id, second_user.id)
            self.assertEqual(first_identity.id, second_identity.id)
            self.assertEqual(second_identity.provider_email, "new@example.com")
            self.assertIsNotNone(first_login)
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(UserIdentityModel)
                ),
                1,
            )

    def test_disabled_user_rejects_new_and_existing_sessions(self):
        cipher = TokenCipher({"v1": b"1" * 32}, "v1")
        with self.factory() as session:
            user, _identity = self.resolve(
                session, "google", "disabled-subject", "disabled@example.com"
            )
            repository = AuthPersistenceRepository(session, cipher)
            connection = repository.upsert_connection(
                tenant_id="legacy-tenant",
                provider="google",
                provider_account_id="disabled-subject",
                connection_purpose="application_login",
                account_email="disabled@example.com",
                access_token="access",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                scopes=["drive"],
                token_type="Bearer",
            )
            session_id, row = repository.create_session(
                connection=connection,
                user={"id": "disabled-subject"},
                ttl_seconds=3600,
                user_id=user.id,
            )
            user.status = "disabled"
            session.commit()
            with self.assertRaises(ApplicationUserInactiveError):
                repository.create_session(
                    connection=connection,
                    user={"id": "disabled-subject"},
                    ttl_seconds=3600,
                    user_id=user.id,
                )
            self.assertIsNone(
                repository.load_session(provider="google", session_id=session_id)
            )
            self.assertIsNotNone(row.revoked_at)

    def test_provider_metadata_is_allowlisted_bounded_and_secret_free(self):
        with self.factory() as session:
            _user, google = self.resolve(
                session, "google", "metadata-subject", "meta@example.com"
            )
            session.commit()
            document = google.provider_metadata_json
            serialized = str(document)
            self.assertEqual(document, {"email_verified": True})
            self.assertLessEqual(len(serialized.encode()), 4096)
            self.assertNotIn("must-not-persist", serialized)
            self.assertNotIn("token", serialized)
            self.assertNotIn("temporary=value", serialized)

    def test_link_identity_is_explicit_and_conflict_safe(self):
        with self.factory() as session:
            first, identity = self.resolve(
                session, "google", "link-subject", "one@example.com"
            )
            second, _ = self.resolve(
                session, "google", "other-subject", "one@example.com"
            )
            linked = IdentityResolutionService(session).link_identity_to_user(
                user_id=first.id,
                provider="google",
                provider_subject="link-subject",
            )
            self.assertEqual(linked.id, identity.id)
            with self.assertRaises(ValueError):
                IdentityResolutionService(session).link_identity_to_user(
                    user_id=second.id,
                    provider="google",
                    provider_subject="link-subject",
                )

    def test_database_constraint_is_final_concurrent_first_login_guard(self):
        with self.factory() as session:
            first, _ = self.resolve(
                session, "google", "race-subject", "first@example.com"
            )
            session.commit()
            second, identity = self.resolve(
                session, "google", "race-subject", "second@example.com"
            )
            session.commit()
            self.assertEqual(first.id, second.id)
            self.assertEqual(identity.provider_subject, "race-subject")
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(UserIdentityModel)
                ),
                1,
            )

    def test_legacy_session_without_user_id_remains_valid(self):
        cipher = TokenCipher({"v1": b"2" * 32}, "v1")
        with self.factory() as session:
            repository = AuthPersistenceRepository(session, cipher)
            connection = repository.upsert_connection(
                tenant_id="legacy-tenant",
                provider="google",
                provider_account_id="legacy-subject",
                connection_purpose="application_login",
                account_email=None,
                access_token="access",
                refresh_token=None,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                scopes=["drive"],
                token_type="Bearer",
            )
            session_id, row = repository.create_session(
                connection=connection,
                user={"id": "legacy-subject"},
                ttl_seconds=3600,
            )
            session.commit()
            loaded = repository.load_session(
                provider="google", session_id=session_id
            )
            self.assertIsNone(row.user_id)
            self.assertIsNone(loaded.user_id)
            self.assertEqual(loaded.tenant_id, "legacy-tenant")


if __name__ == "__main__":
    unittest.main()
