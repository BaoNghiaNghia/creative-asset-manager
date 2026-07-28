import unittest
from unittest.mock import patch

from app.providers.google.auth import DRIVE_READONLY_SCOPE, IDENTITY_SCOPES, oauth_flow


class GoogleOauthScopesTest(unittest.TestCase):
    def test_application_login_does_not_request_drive_scope(self):
        with patch("app.providers.google.auth._settings", return_value=("id", "secret", "https://example.test/callback")), patch("app.providers.google.auth.Flow.from_client_config") as build:
            oauth_flow()
        self.assertEqual(build.call_args.kwargs["scopes"], IDENTITY_SCOPES)
        self.assertNotIn(DRIVE_READONLY_SCOPE, build.call_args.kwargs["scopes"])

    def test_drive_connection_requests_drive_scope(self):
        with patch("app.providers.google.auth._settings", return_value=("id", "secret", "https://example.test/callback")), patch("app.providers.google.auth.Flow.from_client_config") as build:
            oauth_flow(require_drive_scope=True)
        self.assertIn(DRIVE_READONLY_SCOPE, build.call_args.kwargs["scopes"])
