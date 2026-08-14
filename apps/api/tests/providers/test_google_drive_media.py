import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.providers.google.drive import (
    GoogleDriveClient,
    GoogleDriveThumbnailUnavailable,
    ThumbnailLinkCache,
    close_media_stream,
    close_thumbnail_stream,
    open_media_stream,
    open_thumbnail_stream,
    thumbnail_link_cache,
)



class GoogleDriveUploadMimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_infers_avif_mime_from_filename(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "id": "uploaded-id",
            "name": "PHOTO.AVIF",
            "mimeType": "application/octet-stream",
        }
        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=response)
        drive_client = object.__new__(GoogleDriveClient)
        drive_client.client = http_client

        uploaded = await drive_client.upload_file(
            "parent-id", "PHOTO.AVIF", "application/octet-stream", b"avif"
        )

        self.assertEqual(uploaded.mime_type, "image/avif")
        uploaded_file = http_client.post.await_args.kwargs["files"]["file"]
        self.assertEqual(uploaded_file[2], "image/avif")


    async def test_create_folder_uses_drive_folder_mime_and_parent(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "id": "folder-id",
            "name": "New folder",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["parent-id"],
        }
        http_client = MagicMock()
        http_client.post = AsyncMock(return_value=response)
        drive_client = object.__new__(GoogleDriveClient)
        drive_client.client = http_client

        folder = await drive_client.create_folder("parent-id", "New folder")

        self.assertEqual(folder.id, "folder-id")
        request = http_client.post.await_args
        self.assertEqual(request.args[0], "/files")
        self.assertEqual(request.kwargs["json"]["parents"], ["parent-id"])
        self.assertEqual(
            request.kwargs["json"]["mimeType"],
            "application/vnd.google-apps.folder",
        )
        self.assertTrue(request.kwargs["params"]["supportsAllDrives"])

    async def test_update_text_file_uses_media_patch_and_plain_text(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "id": "text-id",
            "name": "notes.txt",
            "mimeType": "text/plain",
            "parents": ["parent-id"],
        }
        http_client = MagicMock()
        http_client.patch = AsyncMock(return_value=response)
        drive_client = object.__new__(GoogleDriveClient)
        drive_client.client = http_client

        updated = await drive_client.update_file_content(
            "text-id", "notes.txt", "text/plain", b"updated content"
        )

        self.assertEqual(updated.id, "text-id")
        request = http_client.patch.await_args
        self.assertIn("/upload/drive/v3/files/text-id", request.args[0])
        self.assertEqual(request.kwargs["params"]["uploadType"], "media")
        self.assertEqual(request.kwargs["headers"]["Content-Type"], "text/plain")
        self.assertEqual(request.kwargs["content"], b"updated content")


class GoogleDriveMediaStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_follows_redirects_for_authenticated_media(self):
        response = MagicMock(status_code=200, headers={})
        response.raise_for_status = MagicMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client) as build:
            returned_client, returned_response = await open_media_stream("token", "file-id", None)

        self.assertIs(returned_client, client)
        self.assertIs(returned_response, response)
        self.assertTrue(build.call_args.kwargs["follow_redirects"])

    async def test_shared_media_client_stays_open_after_stream_close(self):
        response = MagicMock(status_code=200, headers={})
        response.raise_for_status = MagicMock()
        response.aclose = AsyncMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient") as build:
            returned_client, returned_response = await open_media_stream(
                "token", "file-id", None, http_client=client
            )

        self.assertIs(returned_client, client)
        self.assertIs(returned_response, response)
        build.assert_not_called()
        await close_media_stream(client, response, close_client=False)
        response.aclose.assert_awaited_once()
        client.aclose.assert_not_awaited()

    async def test_retries_transient_google_failure_before_streaming(self):
        transient = MagicMock(status_code=503, headers={"retry-after": "0"})
        transient.aclose = AsyncMock()
        success = MagicMock(status_code=200, headers={})
        success.raise_for_status = MagicMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(side_effect=(transient, success))

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client), patch(
            "app.providers.google.drive.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            _, response = await open_media_stream("token", "file-id", "bytes=0-")

        self.assertIs(response, success)
        self.assertEqual(client.send.await_count, 2)
        transient.aclose.assert_awaited_once()
        sleep.assert_awaited_once_with(0.0)

    async def test_non_retryable_provider_error_closes_resources(self):
        response = httpx.Response(403, request=httpx.Request("GET", "https://example.test"))
        response.aclose = AsyncMock()
        client = MagicMock()
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=response)
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client):
            with self.assertRaises(httpx.HTTPStatusError):
                await open_media_stream("token", "file-id", None)

        response.aclose.assert_awaited_once()
        client.aclose.assert_awaited_once()

    async def test_thumbnail_resolves_fresh_provider_link_without_alt_media(self):
        metadata = MagicMock(status_code=200, headers={})
        metadata.raise_for_status = MagicMock()
        metadata.json.return_value = {
            "thumbnailLink": "https://lh3.googleusercontent.test/fresh-thumbnail"
        }
        thumbnail = MagicMock(status_code=200, headers={"content-type": "image/jpeg"})
        thumbnail.raise_for_status = MagicMock()
        thumbnail.aclose = AsyncMock()
        request = MagicMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=metadata)
        client.build_request.return_value = request
        client.send = AsyncMock(return_value=thumbnail)
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client):
            returned_client, returned_response = await open_thumbnail_stream(
                "secret-token", "file-id"
            )

        self.assertIs(returned_client, client)
        self.assertIs(returned_response, thumbnail)
        metadata_params = client.get.await_args.kwargs["params"]
        self.assertEqual(metadata_params["fields"], "thumbnailLink")
        self.assertNotIn("alt", metadata_params)
        client.build_request.assert_called_once_with(
            "GET",
            "https://lh3.googleusercontent.test/fresh-thumbnail",
            headers={"Authorization": "Bearer secret-token"},
        )
        client.send.assert_awaited_once_with(request, stream=True)

        await close_thumbnail_stream(client, thumbnail)
        thumbnail.aclose.assert_awaited_once()
        client.aclose.assert_awaited_once()

    async def test_shared_thumbnail_client_stays_open_after_stream_close(self):
        metadata = MagicMock(status_code=200, headers={})
        metadata.raise_for_status = MagicMock()
        metadata.json.return_value = {"thumbnailLink": "https://images.test/thumb"}
        thumbnail = MagicMock(status_code=200, headers={})
        thumbnail.raise_for_status = MagicMock()
        thumbnail.aclose = AsyncMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=metadata)
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=thumbnail)
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient") as build:
            returned_client, returned_response = await open_thumbnail_stream(
                "token", "file", http_client=client
            )

        self.assertIs(returned_client, client)
        self.assertIs(returned_response, thumbnail)
        build.assert_not_called()
        await close_thumbnail_stream(client, thumbnail, close_client=False)
        thumbnail.aclose.assert_awaited_once()
        client.aclose.assert_not_awaited()

    async def test_missing_provider_thumbnail_closes_client(self):
        metadata = MagicMock(status_code=200, headers={})
        metadata.raise_for_status = MagicMock()
        metadata.json.return_value = {}
        client = MagicMock()
        client.get = AsyncMock(return_value=metadata)
        client.send = AsyncMock()
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client):
            with self.assertRaises(GoogleDriveThumbnailUnavailable):
                await open_thumbnail_stream("secret-token", "file-id")

        client.send.assert_not_awaited()
        client.aclose.assert_awaited_once()

    async def test_thumbnail_link_is_cached_by_tenant_source_and_file(self):
        thumbnail_link_cache.clear()
        metadata = MagicMock(status_code=200, headers={})
        metadata.raise_for_status = MagicMock()
        metadata.json.return_value = {"thumbnailLink": "https://images.test/thumb"}
        thumbnail = MagicMock(status_code=200, headers={})
        thumbnail.raise_for_status = MagicMock()
        thumbnail.aclose = AsyncMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=metadata)
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(return_value=thumbnail)
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client):
            await open_thumbnail_stream("token", "file", cache_key=("tenant", "source", "file"))
            await open_thumbnail_stream("token", "file", cache_key=("tenant", "source", "file"))

        self.assertEqual(client.get.await_count, 1)
        self.assertEqual(client.send.await_count, 2)
        thumbnail_link_cache.clear()

    async def test_expired_cached_thumbnail_is_refreshed_once(self):
        thumbnail_link_cache.clear()
        key = ("tenant", "source", "file")
        thumbnail_link_cache.put(key, "https://images.test/stale")
        metadata = MagicMock(status_code=200, headers={})
        metadata.raise_for_status = MagicMock()
        metadata.json.return_value = {"thumbnailLink": "https://images.test/fresh"}
        stale = MagicMock(status_code=403, headers={})
        stale.aclose = AsyncMock()
        fresh = MagicMock(status_code=200, headers={})
        fresh.raise_for_status = MagicMock()
        fresh.aclose = AsyncMock()
        client = MagicMock()
        client.get = AsyncMock(return_value=metadata)
        client.build_request.return_value = MagicMock()
        client.send = AsyncMock(side_effect=(stale, fresh))
        client.aclose = AsyncMock()

        with patch("app.providers.google.drive.httpx.AsyncClient", return_value=client):
            _, response = await open_thumbnail_stream("token", "file", cache_key=key)

        self.assertIs(response, fresh)
        self.assertEqual(client.get.await_count, 1)
        self.assertEqual(client.send.await_count, 2)
        stale.aclose.assert_awaited_once()
        thumbnail_link_cache.clear()

    def test_thumbnail_cache_is_bounded_and_tenant_scoped(self):
        cache = ThumbnailLinkCache(max_entries=2, ttl_seconds=1800)
        cache.put(("tenant-a", "source", "one"), "one")
        cache.put(("tenant-b", "source", "one"), "other")
        cache.put(("tenant-a", "source", "two"), "two")
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get(("tenant-a", "source", "one")))
        self.assertEqual(cache.get(("tenant-b", "source", "one")), "other")
