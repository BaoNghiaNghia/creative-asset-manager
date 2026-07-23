import base64
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.modules.auth_persistence.service import cookie_options

KEY=base64.urlsafe_b64encode(b"k"*32).decode()

PRODUCTION_HTTP_SETTINGS = {
    "PUBLIC_APP_URL": "https://assets.example.com",
    "CORS_ALLOWED_ORIGINS": "https://assets.example.com",
    "TRUSTED_HOSTS": "api.example.com",
    "API_DOCS_ENABLED": False,
    "DATABASE_URL": "postgresql+psycopg://cam:test@db/cam",
}

class AuthConfigurationTest(unittest.TestCase):
    def test_keys_required_and_production_cookie_is_secure(self):
        with self.assertRaises(ValueError): Settings(PERSISTENT_AUTH_ENABLED=True)
        with self.assertRaises(ValueError):
            Settings(PERSISTENT_AUTH_ENABLED=True,OAUTH_TOKEN_ENCRYPTION_KEYS=f"v1:{KEY}",OAUTH_ACTIVE_KEY_VERSION="v1",APP_ENV="production",AUTH_COOKIE_SECURE=False,**PRODUCTION_HTTP_SETTINGS)
        settings=Settings(PERSISTENT_AUTH_ENABLED=True,OAUTH_TOKEN_ENCRYPTION_KEYS=f"v1:{KEY}",OAUTH_ACTIVE_KEY_VERSION="v1",APP_ENV="production",AUTH_COOKIE_SECURE=True,**PRODUCTION_HTTP_SETTINGS)
        with patch("app.modules.auth_persistence.service.get_settings",return_value=settings):
            options=cookie_options()
        self.assertTrue(options["secure"]); self.assertTrue(options["httponly"]); self.assertEqual(options["samesite"],"lax"); self.assertEqual(options["path"],"/")

    def test_invalid_key_and_cookie_combinations_fail(self):
        with self.assertRaises(ValueError):
            Settings(PERSISTENT_AUTH_ENABLED=True,OAUTH_TOKEN_ENCRYPTION_KEYS="v1:bad",OAUTH_ACTIVE_KEY_VERSION="v1")
        with self.assertRaises(ValueError):
            Settings(PERSISTENT_AUTH_ENABLED=True,OAUTH_TOKEN_ENCRYPTION_KEYS=f"v1:{KEY}",OAUTH_ACTIVE_KEY_VERSION="v2")
        with self.assertRaises(ValueError):
            Settings(PERSISTENT_AUTH_ENABLED=True,OAUTH_TOKEN_ENCRYPTION_KEYS=f"v1:{KEY}",OAUTH_ACTIVE_KEY_VERSION="v1",AUTH_COOKIE_SAMESITE="none",AUTH_COOKIE_SECURE=False)

    def test_development_personal_tenant_is_forbidden_in_production(self):
        with self.assertRaisesRegex(
            ValueError, "DEVELOPMENT_PERSONAL_TENANT_ENABLED"
        ):
            Settings(
                APP_ENV="production",
                DEVELOPMENT_PERSONAL_TENANT_ENABLED=True,
                **PRODUCTION_HTTP_SETTINGS,
            )

    def test_legacy_processing_admin_allowlist_is_forbidden_in_production(self):
        with self.assertRaisesRegex(ValueError, "legacy processing admin allowlist"):
            Settings(
                APP_ENV="production",
                AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=True,
                **PRODUCTION_HTTP_SETTINGS,
            )
        with self.assertRaisesRegex(ValueError, "legacy processing admin allowlist"):
            Settings(
                APP_ENV="production",
                PROCESSING_POLICY_ADMIN_IDS="legacy-admin",
                **PRODUCTION_HTTP_SETTINGS,
            )
        settings = Settings(
            APP_ENV="production",
            AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=False,
            PROCESSING_POLICY_ADMIN_IDS="",
            **PRODUCTION_HTTP_SETTINGS,
        )
        self.assertFalse(settings.AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED)

    def test_auth_signup_defaults_fail_closed_and_domains_are_normalized(self):
        defaults = Settings()
        self.assertFalse(defaults.AUTH_SELF_SIGNUP_ENABLED)
        self.assertEqual(defaults.AUTH_SELF_SIGNUP_DEFAULT_ROLE, "viewer")
        self.assertEqual(defaults.auth_allowed_email_domains, ())
        configured = Settings(
            AUTH_ALLOWED_EMAIL_DOMAINS="Example.COM, studio.test,example.com",
            AUTH_SELF_SIGNUP_DEFAULT_ROLE="reviewer",
        )
        self.assertEqual(configured.AUTH_SELF_SIGNUP_DEFAULT_ROLE, "reviewer")
        self.assertEqual(configured.auth_allowed_email_domains,("example.com", "studio.test", "example.com"))
        for forbidden in ("tenant_admin", "platform_admin"):
            with self.assertRaisesRegex(ValueError, "cannot grant administration"):
                Settings(AUTH_SELF_SIGNUP_DEFAULT_ROLE=forbidden)

    def test_legacy_actor_session_deadline_must_be_timezone_aware(self):
        with self.assertRaisesRegex(ValueError, "AUTH_LEGACY_ACTOR"):
            Settings(AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL="2026-08-01")
        active = Settings(AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL="2999-01-01T00:00:00+00:00")
        self.assertTrue(active.legacy_actor_session_compatibility_enabled)

if __name__=="__main__": unittest.main()
