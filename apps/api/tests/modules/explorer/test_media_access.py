from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.modules.explorer.router import _asset_version_fingerprint, media_access


class AssetVersionFingerprintTests(IsolatedAsyncioTestCase):
    async def test_changes_when_provider_version_changes(self):
        first = _asset_version_fingerprint(
            provider_checksum=None,
            provider_version="v1",
            hashed_provider_checksum=None,
            hashed_provider_version=None,
            source_modified_at=None,
            size_bytes=5,
        )
        second = _asset_version_fingerprint(
            provider_checksum=None,
            provider_version="v2",
            hashed_provider_checksum=None,
            hashed_provider_version=None,
            source_modified_at=None,
            size_bytes=5,
        )
        self.assertIsNotNone(first)
        self.assertNotEqual(first, second)

    async def test_returns_none_without_any_freshness_signal(self):
        self.assertIsNone(
            _asset_version_fingerprint(
                provider_checksum=None,
                provider_version=None,
                hashed_provider_checksum=None,
                hashed_provider_version=None,
                source_modified_at=None,
                size_bytes=None,
            )
        )


class MediaAccessProbeTests(IsolatedAsyncioTestCase):
    async def test_revalidates_scope_without_opening_provider_stream(self):
        authorize = AsyncMock(return_value=("token", "tenant-1", "source-1"))
        with (
            patch("app.modules.explorer.router._authorized_file_context", authorize),
            patch(
                "app.modules.explorer.router._source_asset_version",
                return_value="version-fingerprint",
            ),
        ):
            response = await media_access(
                request=object(),
                item_id="asset-1",
                provider="google-drive",
                session=object(),
                principal=object(),
                external_source_id="source-1",
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-cam-asset-version"], "version-fingerprint")
        authorize.assert_awaited_once()

    async def test_preserves_authorization_failure(self):
        denied = HTTPException(status_code=403, detail={"code": "viewer_folder_scope_denied"})
        authorize = AsyncMock(side_effect=denied)
        with patch("app.modules.explorer.router._authorized_file_context", authorize):
            with self.assertRaises(HTTPException) as raised:
                await media_access(
                    request=object(),
                    item_id="asset-1",
                    provider="google-drive",
                    session=object(),
                    principal=object(),
                    external_source_id="source-1",
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail["code"], "viewer_folder_scope_denied")
