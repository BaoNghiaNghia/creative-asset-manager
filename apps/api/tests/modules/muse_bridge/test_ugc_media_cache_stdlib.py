"""Dependency-light regression coverage for the private UGC R2 derivative pipeline."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import base64
import subprocess
import unittest
from unittest.mock import patch
from types import ModuleType, SimpleNamespace
import os
import sys
from urllib.parse import parse_qs, urlsplit

from botocore.exceptions import ClientError
from app.modules.muse_bridge import ugc_media_cache as m


def not_found():
    return ClientError({"Error": {"Code": "404", "Message": "not found"}},
                       "HeadObject")


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.put_calls = 0

    def head_object(self, *, Bucket, Key):
        row = self.objects.get(Key)
        if not row:
            raise not_found()
        return {
            "Metadata": row["Metadata"],
            "ContentType": row["ContentType"],
            "ContentLength": len(row["Body"]),
            "LastModified": row["LastModified"],
        }

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, Metadata,
                   ContentType, CacheControl):
        assert IfNoneMatch == "*"
        if Key in self.objects:
            raise ClientError({"Error": {"Code": "412", "Message": "PreconditionFailed"}},
                              "PutObject")
        self.put_calls += 1
        self.objects[Key] = {
            "Body": Body,
            "Metadata": Metadata,
            "ContentType": ContentType,
            "LastModified": datetime.now(timezone.utc),
        }
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def config(edge=True):
    return m.UgcCacheConfig(
        bucket="creative-asset-video-cache", endpoint="https://example.r2.invalid",
        access_key="unit-test-only", secret_key="unit-test-only",
        signing_secret="Unit-Test-Valid-Secret-XYZ-2026-0000000000",
        worker_origin="https://cam-r2-video.example.workers.dev",
        edge_enabled=edge,
    )


def atom(name, body=b""):
    return (len(body) + 8).to_bytes(4, "big") + name + body


class UGCPrivateR2Test(unittest.TestCase):
    def setUp(self):
        m._CACHE_STATUS.clear()

    def test_cached_positive_never_outlives_original_30_day_retention(self):
        c = config()
        sha = "a" * 64
        key = m.object_key("boostgame", sha, "playback")
        now = 2_000_000_000
        fake = FakeBucket()
        fake.objects[key] = {
            "Body": b"mp4",
            "Metadata": {"cam-source-sha256": sha},
            "ContentType": "video/mp4",
            "LastModified": datetime.fromtimestamp(
                now - 30 * 86400 + 5, timezone.utc,
            ),
        }
        clock = {"seconds": float(now)}
        with patch.object(m, "_client", return_value=fake), \
             patch.object(m.time, "time", side_effect=lambda: clock["seconds"]), \
             patch.object(m.time, "monotonic", side_effect=lambda: clock["seconds"]):
            self.assertTrue(m._has_object(c, "boostgame", sha, "playback"))
            # A page opened six seconds later must use Drive fallback, not a
            # stale positive result that redirects to an expired R2 object.
            clock["seconds"] += 6
            self.assertFalse(m._has_object(c, "boostgame", sha, "playback"))

    def test_existing_private_r2_canary_is_reused_only_for_boostgame(self):
        fake_module = ModuleType("app.core.config")
        class Secret:
            def get_secret_value(self):
                return "Test-Signing-Secret-For-Unit-Only-2026-ABC"
        settings = SimpleNamespace(
            R2_VIDEO_CACHE_ENABLED=True,
            R2_BUCKET_NAME="creative-asset-video-cache",
            R2_ACCESS_KEY_ID=Secret(), R2_SECRET_ACCESS_KEY=Secret(),
            R2_VIDEO_MEDIA_SIGNING_SECRET=Secret(),
            r2_endpoint_url="https://test.r2.cloudflarestorage.com",
            video_media_base_url="https://cam-r2-video.example.workers.dev",
            video_delivery_configured=True,
        )
        fake_module.get_settings = lambda: settings
        env = {
            "CAM_ETSY_IMAGE_SHARED_R2_ENABLED": "true",
            "CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS": "boostgame",
        }
        with patch.dict(sys.modules, {"app.core.config": fake_module}):
            with patch.dict(os.environ, env, clear=True):
                self.assertIsNone(m.config_for_tenant("foreign-tenant"))
                ok = m.config_for_tenant("boostgame")
                self.assertIsNotNone(ok)
                self.assertFalse(ok.edge_enabled)
                with patch.dict(os.environ, {"CAM_MUSE_UGC_R2_ENABLED":"false"}):
                    self.assertIsNone(m.config_for_tenant("boostgame"))
                with patch.dict(os.environ, {"CAM_MUSE_UGC_R2_CANARY_TENANTS":"someone-else"}):
                    self.assertIsNone(m.config_for_tenant("boostgame"))

    def test_bounded_range_proxy_preserves_206_and_private_headers(self):
        import asyncio
        from unittest.mock import patch
        from io import BytesIO
        sha = "a" * 64
        class Client:
            def get_object(self, *, Bucket, Key, Range):
                self.key = Key
                self.request_range = Range
                return {
                    "Body": BytesIO(b"hello"),
                    "Metadata":{"cam-source-sha256":sha},
                    "ContentType":"video/mp4",
                    "ContentLength":5,
                    "ContentRange":"bytes 0-4/50",
                    "ResponseMetadata":{"HTTPStatusCode":206},
                }
        fake = Client()
        async def verify():
            with patch.object(m, "_client", return_value=fake):
                result = await m.proxy_video(
                    config(), tenant="boostgame", sha=sha, requested_range="bytes=0-4",
                )
                self.assertIsNotNone(result)
                status, headers, stream = result
                self.assertEqual(status, 206)
                self.assertEqual(headers["Content-Range"], "bytes 0-4/50")
                self.assertEqual(headers["Cache-Control"], "private, no-store")
                self.assertEqual(headers["Vary"], "Cookie, Authorization")
                self.assertEqual(headers["X-Accel-Buffering"], "no")
                self.assertEqual(b"".join([part async for part in stream]), b"hello")
                self.assertEqual(fake.key, m.object_key("boostgame", sha, "playback"))
                self.assertEqual(fake.request_range, "bytes=0-4")
        asyncio.run(verify())

    def test_route_metrics_do_not_log_listing_identity(self):
        from app.modules.etsy_spy.request_metrics import route_label
        video = "/api/v1/etsy-spy/video-ugc/4380517795/vertical"
        poster = video + "/poster/640"
        self.assertEqual(route_label("GET", video), "ugc_video")
        self.assertEqual(route_label("GET", poster), "ugc_poster")
        self.assertIsNone(route_label("POST", video))
        self.assertIsNone(route_label("GET", video + "/original"))
        self.assertIsNone(route_label("GET", video + "/poster/1280"))

    def test_exact_immutable_key_namespace(self):
        digest = "ab" * 32
        self.assertEqual(
            m.object_key("boostgame", digest, "playback"),
            f"video-cache/boostgame/{digest}/playback.mp4",
        )
        for size in (320, 640):
            self.assertEqual(m.object_key("boostgame", digest, f"poster{size}"),
                             f"video-cache/boostgame/{digest}/poster-{size}.webp")
        for args in [
            ("../boostgame", digest, "playback"),
            ("boostgame", "not-a-sha", "playback"),
            ("boostgame", digest, "original"),
            ("boostgame", digest, "poster1280"),
        ]:
            with self.assertRaises(ValueError):
                m.object_key(*args)

    def test_faststart_requires_top_level_moov_before_mdat(self):
        ftyp = atom(b"ftyp", b"isom")
        self.assertTrue(m.has_faststart(ftyp + atom(b"moov") + atom(b"mdat", b"x")))
        self.assertFalse(m.has_faststart(ftyp + atom(b"mdat", b"x") + atom(b"moov")))
        self.assertFalse(m.has_faststart(ftyp + atom(b"mdat")))
        self.assertFalse(m.has_faststart(b"malformed"))

    def test_signature_uses_video_worker_canonical_message(self):
        c = config()
        key = m.object_key("boostgame", "a" * 64, "playback")
        signed = m.signed_url(c, key, now=100000)
        parsed = urlsplit(signed)
        query = parse_qs(parsed.query)
        self.assertEqual(query["exp"], ["100540"])
        canonical = f"v1\nread\n/{key}\n100540".encode("ascii")
        expected = base64.urlsafe_b64encode(hmac.new(
            c.signing_secret.encode(), canonical, hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        self.assertEqual(query["sig"], [expected])
        self.assertIsNone(m.signed_url(config(edge=False), key, now=100000))

    def test_r2_create_once_no_refresh_on_repeated_views_or_ingest(self):
        c = config()
        sha = "a" * 64
        fake = FakeBucket()
        self.assertTrue(m._put_if_absent(fake, c, "boostgame", sha, "playback", b"mp4"))
        self.assertEqual(fake.put_calls, 1)
        self.assertTrue(m._put_if_absent(fake, c, "boostgame", sha, "playback", b"mp4"))
        self.assertEqual(fake.put_calls, 1)
        key = m.object_key("boostgame", sha, "playback")
        fake.objects[key]["LastModified"] -= timedelta(days=31)
        self.assertFalse(m._put_if_absent(fake, c, "boostgame", sha, "playback", b"mp4"))
        self.assertEqual(fake.put_calls, 1)

    def test_posters_share_source_sha_and_expire_same_time(self):
        c = config()
        fake = FakeBucket()
        sha = "a" * 64
        for kind in ("poster320", "poster640"):
            self.assertTrue(m._put_if_absent(fake, c, "boostgame", sha, kind, b"RIFF"))
            self.assertTrue(m._head(fake, c, m.object_key("boostgame", sha, kind), sha, kind))
        self.assertEqual(fake.put_calls, 2)

    def test_prepare_real_frame_from_generated_mp4(self):
        fragment = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=12",
             "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-movflags", "frag_keyframe+empty_moov",
             "-f", "mp4", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
        )
        if fragment.returncode != 0 or len(fragment.stdout) < 2048:
            self.skipTest("ffmpeg H264 test codec unavailable")
        playback, posters = m._prepare_derivatives(fragment.stdout)
        self.assertTrue(m.has_faststart(playback))
        self.assertTrue(0 < len(posters.get("poster320", b"")) < 700_000)
        self.assertTrue(0 < len(posters.get("poster640", b"")) < 700_000)

    def test_projection_includes_private_poster_urls_only_for_verified_uploads(self):
        from types import SimpleNamespace
        from app.modules.muse_bridge.ui import video_ugc_view
        job = SimpleNamespace(
            listing_key="4380517795", artwork_sha256="a" * 64, status="submitted",
            output_receipts={"drive_uploads": {
                "9:16": {"storageVerified":True, "storage_provider":"google_drive_managed",
                         "drive_file_id":"drive_file_id123", "drive_folder_id":"drive_folder_id123"},
                "16:9": {"storageVerified":False},
            }},
        )
        result = video_ugc_view(job)
        self.assertEqual(len(result["videos"]), 1)
        self.assertTrue(result["videos"][0]["posterUrl"].endswith("/vertical/poster/640"))
        self.assertTrue(result["videos"][0]["thumbnailUrl"].endswith("/vertical/poster/320"))
        self.assertNotIn("drive_file_id", str(result["videos"]))


if __name__ == "__main__":
    unittest.main()