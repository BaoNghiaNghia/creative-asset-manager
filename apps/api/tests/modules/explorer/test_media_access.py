import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.modules.explorer.router import _asset_version_fingerprint, _authorized_file_context, media_access, media_playback_ticket


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


class SearchMediaAuthorizationTests(IsolatedAsyncioTestCase):
    async def test_pure_viewer_with_search_read_can_open_tenant_wide_search_result(self):
        principal = SimpleNamespace(
            membership_id="membership-1",
            effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"assets.read", "search.read"}),
        )
        session = MagicMock()
        source_context = AsyncMock(
            return_value=("token", "account-1", "tenant-1", "source-1")
        )
        with (
            patch("app.modules.explorer.router._source_context", source_context),
            patch("app.modules.explorer.router.ViewerFolderScopeService") as scope_type,
        ):
            result = await _authorized_file_context(
                request=object(),
                item_id="asset-1",
                provider="onedrive",
                session=session,
                principal=principal,
                external_source_id="source-1",
            )

        self.assertEqual(result, ("token", "tenant-1", "source-1"))
        scope_type.assert_not_called()
        session.close.assert_called_once()

    async def test_pure_viewer_without_search_read_keeps_folder_media_scope(self):
        principal = SimpleNamespace(
            membership_id="membership-1",
            effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"assets.read"}),
        )
        session = MagicMock()
        access = object()
        scope = MagicMock()
        scope.access.return_value = access
        with (
            patch(
                "app.modules.explorer.router._source_context",
                AsyncMock(return_value=("token", "account-1", "tenant-1", "source-1")),
            ),
            patch("app.modules.explorer.router.ViewerFolderScopeService", return_value=scope),
            patch(
                "app.modules.explorer.router._viewer_media_scope_allowed",
                AsyncMock(return_value=False),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await _authorized_file_context(
                    request=object(),
                    item_id="asset-1",
                    provider="google-drive",
                    session=session,
                    principal=principal,
                    external_source_id="source-1",
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail["code"], "viewer_folder_scope_denied")


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


class MediaPlaybackTicketTests(IsolatedAsyncioTestCase):
    async def test_returns_cdn_ticket_for_authorized_linked_video(self):
        source = SimpleNamespace(
            id="source-asset-1",
            tenant_id="tenant-1",
            filename="clip.mp4",
            mime_type="video/mp4",
        )
        link = SimpleNamespace(asset_id="asset-1")
        asset = SimpleNamespace(
            id="asset-1",
            tenant_id="tenant-1",
            mime_type="video/mp4",
            content_hash="a" * 64,
        )
        session = MagicMock()
        session.scalar.side_effect = [source, link, asset]
        authorize = AsyncMock(return_value=("token", "tenant-1", "drive-source-1"))

        class DeliveryResolver:
            def __init__(self, *_args):
                pass

            async def resolve(self, **kwargs):
                self.resolve_kwargs = kwargs
                return SimpleNamespace(
                    url="https://media.example.test/video-cache/tenant-1/clip",
                    expires_at=2_000_000_000,
                )

        with (
            patch("app.modules.explorer.router._authorized_file_context", authorize),
            patch("app.modules.explorer.router.PublicVideoDeliveryResolver", DeliveryResolver),
            patch(
                "app.modules.explorer.router.get_settings",
                return_value=SimpleNamespace(R2_VIDEO_MEDIA_TICKET_TTL_SECONDS=300),
            ),
        ):
            response = await media_playback_ticket(
                request=object(),
                item_id="external-video-1",
                provider="google-drive",
                session=session,
                principal=object(),
                external_source_id="drive-source-1",
            )

        payload = json.loads(response.body)
        self.assertTrue(payload["cdn"])
        self.assertEqual(payload["url"], "https://media.example.test/video-cache/tenant-1/clip")
        self.assertEqual(payload["expires_at"], 2_000_000_000)
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        authorize.assert_awaited_once()

    async def test_falls_back_to_scoped_explorer_stream_when_asset_is_not_video(self):
        source = SimpleNamespace(
            id="source-asset-1",
            tenant_id="tenant-1",
            filename="image.jpg",
            mime_type="image/jpeg",
        )
        link = SimpleNamespace(asset_id="asset-1")
        asset = SimpleNamespace(
            id="asset-1",
            tenant_id="tenant-1",
            mime_type="image/jpeg",
            content_hash="b" * 64,
        )
        session = MagicMock()
        session.scalar.side_effect = [source, link, asset]
        authorize = AsyncMock(return_value=("token", "tenant-1", "drive-source-1"))

        with (
            patch("app.modules.explorer.router._authorized_file_context", authorize),
            patch("app.modules.explorer.router.PublicVideoDeliveryResolver") as resolver,
        ):
            response = await media_playback_ticket(
                request=object(),
                item_id="external/image 1",
                provider="google-drive",
                session=session,
                principal=object(),
                external_source_id="drive-source-1",
            )

        payload = json.loads(response.body)
        self.assertFalse(payload["cdn"])
        self.assertIsNone(payload["expires_at"])
        self.assertEqual(
            payload["url"],
            "/api/explorer/media/external%2Fimage%201?provider=google-drive&external_source_id=drive-source-1",
        )
        resolver.assert_not_called()
