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

if __name__=="__main__": unittest.main()
