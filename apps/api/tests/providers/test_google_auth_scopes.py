import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.providers.google.auth import (
    DRIVE_READONLY_SCOPE,
    DRIVE_WRITE_SCOPE,
    IDENTITY_SCOPES,
    get_connection_access_token,
    oauth_flow,
)


class GoogleOauthScopesTest(unittest.TestCase):
    def test_application_login_does_not_request_drive_scope(self):
        with patch("app.providers.google.auth._settings", return_value=("id", "secret", "https://example.test/callback")), patch("app.providers.google.auth.Flow.from_client_config") as build:
            oauth_flow()
        self.assertEqual(build.call_args.kwargs["scopes"], IDENTITY_SCOPES)
        self.assertNotIn(DRIVE_READONLY_SCOPE, build.call_args.kwargs["scopes"])

    def test_drive_connection_requests_drive_scope(self):
        with patch("app.providers.google.auth._settings", return_value=("id", "secret", "https://example.test/callback")), patch("app.providers.google.auth.Flow.from_client_config") as build:
            oauth_flow(require_drive_scope=True)
        self.assertIn(DRIVE_WRITE_SCOPE, build.call_args.kwargs["scopes"])
        self.assertNotIn(DRIVE_READONLY_SCOPE, build.call_args.kwargs["scopes"])


class GoogleOAuthConnectionScopeTest(unittest.IsolatedAsyncioTestCase):
    async def test_write_operation_rejects_readonly_connection_before_provider_call(self):
        repository = MagicMock()
        repository.load_connection.return_value = SimpleNamespace(
            scopes=(DRIVE_READONLY_SCOPE,),
            expires_at=9_999_999_999,
        )
        context = MagicMock()
        context.__enter__.return_value = repository
        with patch("app.providers.google.auth.auth_repository", return_value=context):
            with self.assertRaises(HTTPException) as raised:
                await get_connection_access_token(
                    "connection-id",
                    require_drive_write_scope=True,
                )
        self.assertEqual(raised.exception.status_code, 403)

    async def test_refresh_waiter_reuses_token_refreshed_by_another_request(self):
        expired = SimpleNamespace(
            tenant_id="tenant-id",
            connection_id="connection-id",
            access_token="expired-token",
            refresh_token="refresh-token",
            scopes=(DRIVE_WRITE_SCOPE,),
            expires_at=0,
        )
        refreshed = SimpleNamespace(
            access_token="fresh-token",
            expires_at=9_999_999_999,
        )
        repository = MagicMock()
        repository.load_connection.side_effect = (expired, refreshed)
        repository.claim_refresh.return_value = False
        context = MagicMock()
        context.__enter__.return_value = repository

        with patch("app.providers.google.auth.auth_repository", return_value=context), patch(
            "app.providers.google.auth.asyncio.sleep",
            return_value=None,
        ):
            token = await get_connection_access_token("connection-id")

        self.assertEqual(token, "fresh-token")
        self.assertEqual(repository.claim_refresh.call_count, 1)
