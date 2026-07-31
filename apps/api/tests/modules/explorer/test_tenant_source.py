import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.modules.explorer.tenant_source import TenantSourceResolver


class TenantSourceResolverTest(unittest.IsolatedAsyncioTestCase):
    def _session(self, sources):
        return SimpleNamespace(scalars=lambda _statement: iter(sources))

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
