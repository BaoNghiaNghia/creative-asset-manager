"""Regression checks for real video-poster fallback and bounded R2 backfill."""
from __future__ import annotations

import hashlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from PIL import Image

from app.modules.muse_bridge import ugc_drive_poster as drive
from app.modules.muse_bridge import ugc_media_cache as r2
from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache

TENANT = "test-tenant"
ARGS = dict(tenant=TENANT, listing_id="4380517795", artwork_sha="a"*64,
            sha="b"*64, aspect="vertical", remote_id="file_id_123456789",
            folder_id="folder_id_123456789", size=320)


def photo():
    image = Image.new("RGB", (240, 480), "#446688")
    image.paste("#e9b389", (0, 0, 120, 240))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class DrivePosterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        drive._CACHE = ThumbnailMemoryCache(max_bytes=16*1024*1024, ttl_seconds=900)
        drive._MISSES.clear()

    def test_host_allowlist(self):
        url = "https://lh3.googleusercontent.com/example"
        self.assertEqual(drive.safe_thumbnail_url(url), url)
        for bad in ("http://lh3.googleusercontent.com/example",
                    "https://googleusercontent.com.evil.example/x",
                    "https://evil.example/x",
                    "https://user@lh3.googleusercontent.com/x",
                    "https://lh3.googleusercontent.com:8080/x",
                    "https://lh3.googleusercontent.com/x#frag",
                    "https://lh3.googleusercontent.com/\nx"):
            self.assertIsNone(drive.safe_thumbnail_url(bad))

    def test_webp_dimensions_and_validation(self):
        payload = drive.encode_poster(photo(), 320)
        self.assertIsNotNone(payload)
        with Image.open(io.BytesIO(payload)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (160, 320))
        self.assertIsNone(drive.encode_poster(b"not an image", 320))
        self.assertIsNone(drive.encode_poster(photo(), 2000))

    async def test_verified_read_never_fetches_mp4_and_caches_bytes(self):
        from app.modules.etsy_spy import imagegen_drive, listing_drive_folders
        real_client = httpx.AsyncClient
        requests = []
        def handle(request):
            requests.append(str(request.url))
            self.assertEqual(request.headers["Authorization"], "Bearer fixture-token")
            if request.url.host == "www.googleapis.com":
                self.assertEqual(request.url.params["fields"], "thumbnailLink")
                return httpx.Response(200, json={"thumbnailLink": "https://lh3.googleusercontent.com/example"})
            return httpx.Response(200, content=photo(), headers={"Content-Type":"image/png"})
        def client(**kw):
            return real_client(transport=httpx.MockTransport(handle), **kw)
        provider = SimpleNamespace(get_access_token=AsyncMock(return_value="fixture-token"))
        proof = AsyncMock()
        with patch.object(imagegen_drive,"provider_and_folder", return_value=(provider, listing_drive_folders.ROOT_FOLDER_ID)), patch.object(listing_drive_folders,"verify_listing_file_provenance", proof), patch.object(drive.httpx,"AsyncClient",side_effect=client):
            first = await drive.read_drive_poster(**ARGS)
            second = await drive.read_drive_poster(**ARGS)
            self.assertIsNotNone(first)
            self.assertEqual(first, second)
            self.assertEqual(len(requests), 2)
            proof.assert_awaited_once_with(provider=provider, remote_file_id=ARGS["remote_id"],
                folder_id=ARGS["folder_id"], tenant_id=TENANT,
                asset_id="muse-ugc:4380517795:"+"a"*64+":vertical", sha256="b"*64)
            # Tenant mismatch cannot reuse the successful tenant's memory cache.
            proof.side_effect = ValueError("wrong tenant")
            self.assertIsNone(await drive.read_drive_poster(**{**ARGS,"tenant":"other-tenant"}))
            self.assertEqual(len(requests),2)

    async def test_failed_proof_blocks_all_remote_reads_and_backs_off(self):
        from app.modules.etsy_spy import imagegen_drive, listing_drive_folders
        provider = SimpleNamespace(get_access_token=AsyncMock())
        proof = AsyncMock(side_effect=ValueError("invalid proof"))
        with patch.object(imagegen_drive,"provider_and_folder",return_value=(provider,listing_drive_folders.ROOT_FOLDER_ID)), patch.object(listing_drive_folders,"verify_listing_file_provenance",proof):
            self.assertIsNone(await drive.read_drive_poster(**ARGS))
            self.assertIsNone(await drive.read_drive_poster(**ARGS))
            proof.assert_awaited_once()
            provider.get_access_token.assert_not_awaited()


class PosterBackfillTest(unittest.IsolatedAsyncioTestCase):
    async def test_poster_only_mode_never_reads_or_writes_video(self):
        content = b"x"*4096
        sha = hashlib.sha256(content).hexdigest()
        with patch.object(r2,"_client") as client, patch.object(r2,"_put_if_absent") as put:
            self.assertEqual(await r2.stage_posters(Mock(),tenant=TENANT,sha=sha,mp4=content),{})
            client.assert_not_called()
            put.assert_not_called()
