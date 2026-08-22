import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.modules.explorer.cache import drive_source_cache, invalidate_drive_source
from app.modules.explorer.tenant_source import TenantSourceResolver


class TenantSourceResolverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        drive_source_cache.clear()

    def tearDown(self):
        drive_source_cache.clear()

    def _session(self, sources):
        return SimpleNamespace(
            scalars=lambda _statement: iter(sources),
            close=lambda: None,
        )

    async def test_uses_tenant_configured_connection_not_viewer_identity(self):
        source = SimpleNamespace(
            id="source-a",
            source_metadata={
                "oauth_connection_id": "connection-admin",
                "provider_account_id": "admin-google-subject",
            },
        )
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(return_value="tenant-drive-token"),
        ) as token:
            access = await TenantSourceResolver(self._session([source])).google_drive(
                tenant_id="tenant-a"
            )
        self.assertEqual(access.access_token, "tenant-drive-token")
        self.assertEqual(access.provider_account_id, "admin-google-subject")
        token.assert_awaited_once_with("connection-admin")

    async def test_metadata_cache_is_tenant_scoped_and_avoids_second_query(self):
        source = SimpleNamespace(
            id="source-a",
            source_metadata={
                "oauth_connection_id": "connection-admin",
                "provider_account_id": "account-a",
            },
        )
        first_session = self._session([source])
        second_session = SimpleNamespace(
            scalars=lambda _statement: (_ for _ in ()).throw(
                AssertionError("source metadata should be cached")
            ),
            close=lambda: None,
        )
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(return_value="token"),
        ):
            first = await TenantSourceResolver(first_session).google_drive(
                tenant_id="tenant-a"
            )
            second = await TenantSourceResolver(second_session).google_drive(
                tenant_id="tenant-a"
            )
        self.assertEqual(first.external_source_id, second.external_source_id)

        other = SimpleNamespace(
            id="source-b",
            source_metadata={"oauth_connection_id": "connection-b"},
        )
        third = await self._resolve_with_token("tenant-b", [other])
        self.assertEqual(third.external_source_id, "source-b")

    async def _resolve_with_token(self, tenant_id, sources):
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(return_value="token"),
        ):
            return await TenantSourceResolver(self._session(sources)).google_drive(
                tenant_id=tenant_id
            )

    async def test_source_metadata_invalidation_refreshes_reconnect(self):
        original = SimpleNamespace(
            id="source-a",
            source_metadata={"oauth_connection_id": "connection-old"},
        )
        replacement = SimpleNamespace(
            id="source-a",
            source_metadata={"oauth_connection_id": "connection-new"},
        )
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(side_effect=["old-token", "new-token"]),
        ) as token:
            await TenantSourceResolver(self._session([original])).google_drive(
                tenant_id="tenant-a", external_source_id="source-a"
            )
            invalidate_drive_source(
                tenant_id="tenant-a", external_source_id="source-a"
            )
            refreshed = await TenantSourceResolver(
                self._session([replacement])
            ).google_drive(
                tenant_id="tenant-a", external_source_id="source-a"
            )
        self.assertEqual(refreshed.access_token, "new-token")
        self.assertEqual(
            [call.args[0] for call in token.await_args_list],
            ["connection-old", "connection-new"],
        )

    async def test_write_operation_requires_write_scoped_connection(self):
        source = SimpleNamespace(
            id="source-a",
            source_metadata={"oauth_connection_id": "connection-admin"},
        )
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(return_value="tenant-drive-token"),
        ) as token:
            await TenantSourceResolver(self._session([source])).google_drive(
                tenant_id="tenant-a",
                require_drive_write_scope=True,
            )
        token.assert_awaited_once_with(
            "connection-admin",
            require_drive_write_scope=True,
        )

    async def test_uses_the_single_default_when_multiple_tenant_sources_exist(self):
        sources = [
            SimpleNamespace(id="older", source_metadata={"oauth_connection_id": "older"}),
            SimpleNamespace(id="default", source_metadata={"oauth_connection_id": "default", "is_default": True}),
        ]
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(return_value="tenant-drive-token"),
        ) as token:
            access = await TenantSourceResolver(self._session(sources)).google_drive(
                tenant_id="tenant-a"
            )
        self.assertEqual(access.external_source_id, "default")
        token.assert_awaited_once_with("default")

    async def test_uses_most_recent_legacy_source_when_no_default_is_marked(self):
        earlier = datetime(2026, 7, 1, tzinfo=timezone.utc)
        later = datetime(2026, 7, 2, tzinfo=timezone.utc)
        sources = [
            SimpleNamespace(id="older", created_at=earlier, updated_at=earlier, source_metadata={"oauth_connection_id": "older"}),
            SimpleNamespace(id="newer", created_at=earlier, updated_at=later, source_metadata={"oauth_connection_id": "newer"}),
        ]
        with patch(
            "app.modules.explorer.tenant_source.get_connection_access_token",
            new=AsyncMock(return_value="tenant-drive-token"),
        ) as token:
            access = await TenantSourceResolver(self._session(sources)).google_drive(
                tenant_id="tenant-a"
            )
        self.assertEqual(access.external_source_id, "newer")
        token.assert_awaited_once_with("newer")

    async def test_rejects_multiple_default_tenant_sources(self):
        sources = [
            SimpleNamespace(id="one", source_metadata={"oauth_connection_id": "one", "is_default": True}),
            SimpleNamespace(id="two", source_metadata={"oauth_connection_id": "two", "is_default": True}),
        ]
        with self.assertRaises(HTTPException) as raised:
            await TenantSourceResolver(self._session(sources)).google_drive(
                tenant_id="tenant-a"
            )
        self.assertEqual(raised.exception.status_code, 409)

    async def test_missing_source_is_not_a_viewer_token_fallback(self):
        with self.assertRaises(HTTPException) as raised:
            await TenantSourceResolver(self._session([])).google_drive(
                tenant_id="tenant-a"
            )
        self.assertEqual(raised.exception.status_code, 404)
