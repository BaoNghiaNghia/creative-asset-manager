import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import Settings
from app.modules.storage.managed_oauth import (
    MANAGED_STORAGE_OAUTH_PROVIDER,
    ManagedStorageCredentialValidationError,
    check_managed_storage_refresh_token,
    managed_storage_oauth_status,
    persist_managed_storage_connection,
    resolve_managed_storage_credential,
)

TEST_TOKEN_KEY = "v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class ManagedStorageCredentialResolverTest(unittest.TestCase):
    def settings(self):
        return Settings(
            PERSISTENT_AUTH_ENABLED=True,
            OAUTH_TOKEN_ENCRYPTION_KEYS=TEST_TOKEN_KEY,
            OAUTH_ACTIVE_KEY_VERSION="v1",
            GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="folder-active",
            GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN="env-access",
            GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN="env-refresh",
        )

    def repository_context(self, *, connection=None, row=None):
        repository = MagicMock()
        repository.session.scalars.return_value = [row] if row else []
        repository.load_connection.return_value = connection

        @contextmanager
        def context():
            yield repository

        return context

    def test_database_connection_is_preferred_over_environment_token(self):
        row = SimpleNamespace(
            id="connection-id",
            provider_metadata_json={"root_folder_id": "folder-active"},
            account_email="managed@example.com",
            updated_at=datetime.now(timezone.utc),
            status="active",
        )
        connection = SimpleNamespace(
            access_token="db-access",
            refresh_token="db-refresh",
        )
        with patch(
            "app.modules.storage.managed_oauth.auth_repository",
            self.repository_context(connection=connection, row=row),
        ):
            result = resolve_managed_storage_credential(self.settings())
            status = managed_storage_oauth_status(self.settings())
        self.assertEqual(result.access_token, "db-access")
        self.assertEqual(result.refresh_token, "db-refresh")
        self.assertTrue(status["connected"])
        self.assertEqual(status["account_email"], "m***@example.com")

    def test_environment_token_is_not_used_as_a_runtime_fallback(self):
        with patch(
            "app.modules.storage.managed_oauth.auth_repository",
            self.repository_context(),
        ):
            result = resolve_managed_storage_credential(self.settings())
            status = managed_storage_oauth_status(self.settings())
        self.assertIsNone(result.refresh_token)
        self.assertIsNone(result.access_token)
        self.assertFalse(status["connected"])
        self.assertTrue(status["reconnect_required"])
        self.assertEqual(status["source"], "none")


class ManagedStorageManualCredentialTest(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return Settings(
            PERSISTENT_AUTH_ENABLED=True,
            OAUTH_TOKEN_ENCRYPTION_KEYS=TEST_TOKEN_KEY,
            OAUTH_ACTIVE_KEY_VERSION="v1",
            GOOGLE_CLIENT_ID="client-id",
            GOOGLE_CLIENT_SECRET="client-secret",
            GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="folder-active",
        )

    @staticmethod
    def response(status_code, payload):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = payload
        return response

    def google_client(self, *, token_status=200, folder_can_write=True):
        client = AsyncMock()
        client.post.return_value = self.response(
            token_status,
            {
                "access_token": "short-lived-access",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/drive",
            } if token_status == 200 else {"error": "invalid_grant"},
        )
        client.get.side_effect = [
            self.response(200, {
                "user": {
                    "permissionId": "google-account-id",
                    "emailAddress": "managed@example.com",
                }
            }),
            self.response(200, {
                "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
                "capabilities": {
                    "canAddChildren": folder_can_write,
                    "canDeleteChildren": folder_can_write,
                },
            }),
        ]
        context = AsyncMock()
        context.__aenter__.return_value = client
        return context

    async def test_valid_token_can_be_checked_without_persisting(self):
        with (
            patch(
                "app.modules.storage.managed_oauth.httpx.AsyncClient",
                return_value=self.google_client(),
            ),
            patch(
                "app.modules.storage.managed_oauth._persist_validated_connection"
            ) as persist,
        ):
            result = await check_managed_storage_refresh_token(
                self.settings(),
                "refresh-token-value",
                tenant_id="tenant-a",
                initiating_user_id="admin-a",
                save=False,
            )
        self.assertEqual(result.account_email, "m***@example.com")
        self.assertFalse(result.saved)
        persist.assert_not_called()

    async def test_valid_token_is_persisted_only_after_folder_check(self):
        with (
            patch(
                "app.modules.storage.managed_oauth.httpx.AsyncClient",
                return_value=self.google_client(),
            ),
            patch(
                "app.modules.storage.managed_oauth._persist_validated_connection"
            ) as persist,
        ):
            result = await check_managed_storage_refresh_token(
                self.settings(),
                "refresh-token-value",
                tenant_id="tenant-a",
                initiating_user_id="admin-a",
                save=True,
            )
        self.assertTrue(result.saved)
        self.assertEqual(persist.call_args.kwargs["refresh_token"], "refresh-token-value")
        self.assertEqual(persist.call_args.kwargs["root_folder_id"], "folder-active")

    async def test_rejected_token_is_never_persisted(self):
        with (
            patch(
                "app.modules.storage.managed_oauth.httpx.AsyncClient",
                return_value=self.google_client(token_status=400),
            ),
            patch(
                "app.modules.storage.managed_oauth._persist_validated_connection"
            ) as persist,
            self.assertRaises(ManagedStorageCredentialValidationError),
        ):
            await check_managed_storage_refresh_token(
                self.settings(),
                "rejected-refresh-token",
                tenant_id="tenant-a",
                initiating_user_id="admin-a",
                save=True,
            )
        persist.assert_not_called()


class ManagedStoragePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_requires_a_refresh_token(self):
        credentials = SimpleNamespace(
            token="access",
            refresh_token=None,
            granted_scopes=("https://www.googleapis.com/auth/drive",),
        )
        with self.assertRaisesRegex(PermissionError, "refresh token"):
            await persist_managed_storage_connection(
                credentials,
                tenant_id="tenant-a",
                initiating_user_id="admin-a",
                root_folder_id="folder-active",
            )

    async def test_valid_folder_connection_is_encrypted_through_repository(self):
        credentials = SimpleNamespace(
            token="access-token",
            refresh_token="refresh-token",
            expiry=datetime.now(timezone.utc),
            granted_scopes=("https://www.googleapis.com/auth/drive",),
        )
        profile = MagicMock()
        profile.raise_for_status.return_value = None
        profile.json.return_value = {"sub": "account-a", "email": "managed@example.com"}
        folder = MagicMock()
        folder.raise_for_status.return_value = None
        folder.json.return_value = {
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
            "capabilities": {"canAddChildren": True, "canDeleteChildren": True},
        }
        client = AsyncMock()
        client.get.side_effect = [profile, folder]
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        repository = MagicMock()
        connection = SimpleNamespace(id="connection-id")
        repository.upsert_connection.return_value = connection
        repository.session.scalars.return_value = []

        @contextmanager
        def repository_context():
            yield repository

        with (
            patch("app.modules.storage.managed_oauth.httpx.AsyncClient", return_value=client_context),
            patch("app.modules.storage.managed_oauth.auth_repository", repository_context),
        ):
            result = await persist_managed_storage_connection(
                credentials,
                tenant_id="tenant-a",
                initiating_user_id="admin-a",
                root_folder_id="folder-active",
            )

        self.assertTrue(result["connected"])
        values = repository.upsert_connection.call_args.kwargs
        self.assertEqual(values["provider"], MANAGED_STORAGE_OAUTH_PROVIDER)
        self.assertEqual(values["refresh_token"], "refresh-token")
        self.assertEqual(values["provider_metadata"]["root_folder_id"], "folder-active")


if __name__ == "__main__":
    unittest.main()
