import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.providers.google.auth import (
    DRIVE_READONLY_SCOPE,
    DRIVE_WRITE_SCOPE,
    IDENTITY_SCOPES,
    get_access_token,
    get_connection_access_token,
    oauth_flow,
    resolve_granted_scopes,
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


    def test_granted_scopes_are_normalized_and_token_response_is_supported(self):
        credentials = SimpleNamespace(granted_scopes=None, scopes=None)
        self.assertEqual(
            resolve_granted_scopes(
                credentials,
                {"scope": f"{DRIVE_WRITE_SCOPE} {DRIVE_WRITE_SCOPE}"},
            ),
            (DRIVE_WRITE_SCOPE,),
        )

    def test_granted_scopes_take_precedence_over_requested_scopes(self):
        credentials = SimpleNamespace(
            granted_scopes=(DRIVE_WRITE_SCOPE,),
            scopes=(DRIVE_READONLY_SCOPE,),
        )
        self.assertEqual(resolve_granted_scopes(credentials), (DRIVE_WRITE_SCOPE,))


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


    async def test_expired_session_refresh_uses_persisted_connection_scopes(self):
        expired_session = SimpleNamespace(
            tenant_id="tenant-id",
            connection_id="connection-id",
            access_token="expired",
            refresh_token="refresh",
            expires_at=0,
        )
        persisted_connection = SimpleNamespace(scopes=(DRIVE_WRITE_SCOPE,))
        refreshed_credentials = SimpleNamespace(
            token="fresh",
            refresh_token="rotated",
            expiry=None,
            granted_scopes=None,
            scopes=(DRIVE_WRITE_SCOPE,),
            refresh=lambda *_args: None,
        )
        repository = MagicMock()
        repository.claim_refresh.return_value = True
        repository.load_connection.return_value = persisted_connection
        context = MagicMock()
        context.__enter__.return_value = repository
        with (
            patch("app.providers.google.auth.get_session_by_id", return_value=expired_session),
            patch("app.providers.google.auth.get_settings", return_value=SimpleNamespace(AUTH_REFRESH_LEASE_SECONDS=60)),
            patch("app.providers.google.auth._settings", return_value=("id", "secret", "https://example.test/callback")),
            patch("app.providers.google.auth.auth_repository", return_value=context),
            patch("app.providers.google.auth.Credentials", return_value=refreshed_credentials) as credentials,
            patch("app.providers.google.auth.run_in_threadpool", new=AsyncMock()),
        ):
            token = await get_access_token(SimpleNamespace(cookies={"cam_google_session": "session-id"}))

        self.assertEqual(token, "fresh")
        self.assertEqual(credentials.call_args.kwargs["scopes"], [DRIVE_WRITE_SCOPE])
        self.assertEqual(repository.finish_refresh.call_args.kwargs["scopes"], [DRIVE_WRITE_SCOPE])
        self.assertEqual(credentials.call_args.kwargs["token_uri"], "https://oauth2.googleapis.com/token")
        self.assertNotIn("[https://", credentials.call_args.kwargs["token_uri"])

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
