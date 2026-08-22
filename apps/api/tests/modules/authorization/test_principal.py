import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.common.cache import BoundedTTLCache
from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.model import (
    AuthSessionModel,
    OAuthConnectionModel,
    UserIdentityModel,
    UserModel,
)
from app.modules.auth_persistence.repository import PersistentCloudSession, digest
from app.modules.auth_persistence.tenant_membership import TenantMembershipService
from app.modules.authorization.model import RoleModel
from app.modules.authorization.principal_cache import principal_cache
from app.modules.authorization.platform_admin import PlatformAdminService
from app.modules.authorization.principal import (
    require_all_permissions,
    require_any_permission,
    require_authenticated_principal,
    require_permission,
    require_tenant_scope,
)
from app.modules.authorization.router import identity as identity_endpoint
from app.modules.authorization.seed import seed_tenant_rbac
from app.modules.authorization.service import TenantAuthorizationService


class CurrentPrincipalTest(unittest.TestCase):
    def setUp(self):
        principal_cache.clear()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        with self.factory() as database:
            self.user = UserModel(
                primary_email="member@example.com",
                display_name="Member",
                status="active",
            )
            database.add(self.user)
            database.flush()
            self.identity = UserIdentityModel(
                user_id=self.user.id,
                provider="google",
                provider_subject="google-subject",
                provider_email="member@example.com",
                provider_metadata_json={},
            )
            database.add(self.identity)
            memberships = TenantMembershipService(database)
            self.tenant = memberships.create_tenant(name="Tenant", slug="tenant")
            self.membership = memberships.add_member(
                tenant_id=self.tenant.id, user_id=self.user.id
            )
            seed_tenant_rbac(database, self.tenant.id)
            database.commit()
        self.cloud = PersistentCloudSession(
            session_id_hash="safe-session-hash",
            connection_id="connection",
            tenant_id="legacy-provider-tenant",
            user_id=self.user.id,
            active_tenant_id=self.tenant.id,
            provider="google",
            access_token="secret-access-token",
            refresh_token="secret-refresh-token",
            expires_at=9999999999,
            user={"id": "google-subject", "email": "member@example.com"},
        )
        self.request = SimpleNamespace(cookies={})

    def tearDown(self):
        principal_cache.clear()
        self.engine.dispose()

    def assign(self, role_key):
        with self.factory() as database:
            role = database.scalar(
                select(RoleModel).where(
                    RoleModel.tenant_id == self.tenant.id,
                    RoleModel.role_key == role_key,
                )
            )
            TenantAuthorizationService(database).assign_role(
                tenant_id=self.tenant.id,
                membership_id=self.membership.id,
                role_id=role.id,
            )
            database.commit()

    def load(self, *, settings=None):
        runtime_settings = settings or Settings()
        with (
            patch(
                "app.modules.authorization.principal.SessionLocal", self.factory
            ),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=self.cloud,
            ),
            patch(
                "app.modules.authorization.principal.get_microsoft_session",
                return_value=None,
            ),
            patch(
                "app.modules.authorization.principal.get_settings",
                return_value=runtime_settings,
            ),
        ):
            return require_authenticated_principal(self.request)

    def error_code(self, callback):
        with self.assertRaises(HTTPException) as captured:
            callback()
        return captured.exception.status_code, captured.exception.detail["code"]

    def test_unauthenticated(self):
        with (
            patch("app.modules.authorization.principal.SessionLocal", self.factory),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=None,
            ),
            patch(
                "app.modules.authorization.principal.get_microsoft_session",
                return_value=None,
            ),
        ):
            self.assertEqual(
                self.error_code(
                    lambda: require_authenticated_principal(self.request)
                ),
                (401, "authentication_required"),
            )

    def test_repeated_resolution_uses_cached_principal_and_tenant_key(self):
        self.assign("viewer")
        first = self.load()
        with patch(
            "app.modules.authorization.principal.SessionLocal",
            side_effect=AssertionError("principal DB resolution should be cached"),
        ):
            with (
                patch(
                    "app.modules.authorization.principal.get_google_session",
                    return_value=self.cloud,
                ),
                patch(
                    "app.modules.authorization.principal.get_microsoft_session",
                    return_value=None,
                ),
            ):
                second = require_authenticated_principal(self.request)
        self.assertIs(second, first)

        self.cloud.active_tenant_id = "different-tenant"
        self.assertEqual(
            self.error_code(self.load),
            (403, "tenant_membership_required"),
        )

    def test_expired_principal_re_resolves_and_fails_closed(self):
        self.assign("viewer")
        clock = [100.0]
        original = principal_cache._cache
        principal_cache._cache = BoundedTTLCache(
            max_entries=8, ttl_seconds=20, clock=lambda: clock[0]
        )
        try:
            self.load()
            with self.factory() as database:
                database.get(UserModel, self.user.id).status = "disabled"
                database.commit()
            clock[0] += 21
            self.assertEqual(self.error_code(self.load), (403, "user_disabled"))
        finally:
            principal_cache._cache = original

    def test_role_change_invalidates_cached_permissions(self):
        self.assign("viewer")
        viewer = self.load()
        self.assertEqual(viewer.effective_roles, {"viewer"})
        self.assign("operator")
        updated = self.load()
        self.assertEqual(updated.effective_roles, {"viewer", "operator"})

    def test_session_invalidation_removes_cached_principal(self):
        self.assign("viewer")
        first = self.load()
        self.assertEqual(
            principal_cache.invalidate_session(first.session_id), 1
        )
        with self.factory() as database:
            database.get(UserModel, self.user.id).status = "disabled"
            database.commit()
        self.assertEqual(self.error_code(self.load), (403, "user_disabled"))

    def test_authenticated_active_user_and_external_identity(self):
        self.assign("viewer")
        principal = self.load()
        self.assertEqual(principal.user_id, self.user.id)
        self.assertEqual(principal.active_tenant_id, self.tenant.id)
        self.assertEqual(principal.membership_id, self.membership.id)
        self.assertEqual(principal.external_identity.provider_subject, "google-subject")
        self.assertEqual(principal.effective_roles, {"viewer"})
        self.assertEqual(principal.effective_permissions, {"assets.read", "assets.upload", "assets.delete", "search.read"})
        self.assertEqual(principal.session_id, "safe-session-hash")

    def test_disabled_user(self):
        with self.factory() as database:
            row = database.get(UserModel, self.user.id)
            row.status = "disabled"
            database.commit()
        self.assertEqual(
            self.error_code(self.load),
            (403, "user_disabled"),
        )

    def test_disabled_user_after_repository_revokes_session(self):
        with self.factory() as database:
            user = database.get(UserModel, self.user.id)
            user.status = "disabled"
            connection = OAuthConnectionModel(
                id="disabled-connection",
                tenant_id="legacy-provider-tenant",
                provider="google",
                provider_account_id="google-subject",
                key_version="v1",
                status="active",
            )
            database.add(connection)
            database.flush()
            database.add(
                AuthSessionModel(
                    session_id_hash=digest("revoked-cookie"),
                    user_id=self.user.id,
                    active_tenant_id=self.tenant.id,
                    tenant_id=connection.tenant_id,
                    provider=connection.provider,
                    connection_id=connection.id,
                    user_json={"id": "google-subject"},
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    revoked_at=datetime.now(timezone.utc),
                )
            )
            database.commit()
        request = SimpleNamespace(cookies={"cam_google_session": "revoked-cookie"})
        with (
            patch("app.modules.authorization.principal.SessionLocal", self.factory),
            patch("app.modules.authorization.principal.get_google_session", return_value=None),
            patch("app.modules.authorization.principal.get_microsoft_session", return_value=None),
        ):
            self.assertEqual(
                self.error_code(lambda: require_authenticated_principal(request)),
                (403, "user_disabled"),
            )

    def test_no_membership_and_invalid_active_tenant(self):
        with self.factory() as database:
            membership = database.get(type(self.membership), self.membership.id)
            membership.status = "removed"
            database.commit()
        self.assertEqual(
            self.error_code(self.load),
            (403, "tenant_membership_required"),
        )

    def test_invalid_active_tenant(self):
        self.cloud.active_tenant_id = "tenant-not-owned-by-user"
        self.assertEqual(
            self.error_code(self.load),
            (403, "tenant_membership_required"),
        )

    def test_permission_dependencies_and_multiple_roles(self):
        self.assign("viewer")
        self.assign("operator")
        principal = self.load()
        self.assertEqual(principal.effective_roles, {"viewer", "operator"})
        self.assertIs(require_permission("assets.read")(principal), principal)
        self.assertIs(
            require_any_permission("ai_provider.configure", "ai_analysis.run")(
                principal
            ),
            principal,
        )
        self.assertIs(
            require_all_permissions("assets.read", "ai_analysis.run")(principal),
            principal,
        )
        status, code = self.error_code(
            lambda: require_permission("ai_provider.configure")(principal)
        )
        self.assertEqual((status, code), (403, "permission_required"))

    def test_durable_platform_admin_bypasses_tenant_permission(self):
        with self.factory() as database:
            PlatformAdminService(database).grant(
                user_id=self.user.id,
                granted_by_user_id=None,
                reason="initial production bootstrap",
            )
            database.commit()
        principal = self.load()
        self.assertTrue(principal.platform_admin)
        self.assertEqual(principal.authorization_source, "durable_platform_admin")
        self.assertIs(require_permission("search.index.activate")(principal), principal)

    def test_compatibility_allowlist_is_default_off_and_explicit(self):
        default_principal = self.load(
            settings=Settings(PROCESSING_POLICY_ADMIN_IDS="google-subject")
        )
        self.assertFalse(default_principal.platform_admin)
        principal_cache.invalidate_session(default_principal.session_id)
        compatible = self.load(
            settings=Settings(
                PROCESSING_POLICY_ADMIN_IDS="google-subject",
                AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=True,
            )
        )
        self.assertTrue(compatible.platform_admin)
        self.assertEqual(
            compatible.authorization_source,
            "deprecated_processing_admin_allowlist",
        )

    def test_tenant_mismatch_and_repository_tenant_filter(self):
        self.assign("viewer")
        principal = self.load()
        self.assertEqual(
            self.error_code(lambda: require_tenant_scope(principal, "other-tenant")),
            (403, "tenant_mismatch"),
        )
        with self.factory() as database:
            effective = TenantAuthorizationService(database).get_effective_permissions(
                tenant_id="other-tenant", user_id=self.user.id
            )
            self.assertEqual(effective.permissions, frozenset())

    def test_identity_endpoint_excludes_sessions_and_credentials(self):
        self.assign("tenant_admin")
        principal = self.load()
        with patch(
            "app.modules.authorization.router.SessionLocal", self.factory
        ):
            payload = identity_endpoint(principal)
        self.assertEqual(payload["user_id"], self.user.id)
        self.assertEqual(payload["actor_id"], "google-subject")
        self.assertTrue(payload["is_processing_admin"])
        serialized = str(payload).lower()
        self.assertNotIn("session", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret", serialized)

    def test_flag_validation(self):
        self.assertFalse(Settings().AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED)
        with self.assertRaises(ValueError):
            Settings(AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED="yes")


if __name__ == "__main__":
    unittest.main()
