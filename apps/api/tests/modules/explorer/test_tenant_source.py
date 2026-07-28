import unittest
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

    async def test_rejects_ambiguous_tenant_sources_without_explicit_source(self):
        sources = [
            SimpleNamespace(id="one", source_metadata={"oauth_connection_id": "one"}),
            SimpleNamespace(id="two", source_metadata={"oauth_connection_id": "two"}),
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
