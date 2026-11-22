"""Dependency-light coverage for private Etsy Listing Image R2 hot cache.

Run with PYTHONPATH=apps/api python3 -m unittest discover -s
apps/api/tests/modules/etsy_spy -p test_listing_shared_r2_stdlib.py -v
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest.mock import patch

# Source checkout has no project dependencies. Do not connect to cloud services.
if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        sys.modules["httpx"] = types.ModuleType("httpx")
if "app.core.config" not in sys.modules:
    try:
        import app.core.config  # noqa: F401
    except ImportError:
        config_stub = types.ModuleType("app.core.config")
        config_stub.get_settings = lambda: None
        sys.modules["app.core.config"] = config_stub

from app.modules.etsy_spy import listing_image_shared_r2 as cache
from app.modules.etsy_spy.thumbnail_memory_cache import ThumbnailMemoryCache


class PrivateR2HotCacheTest(unittest.TestCase):
    def setUp(self):
        self.config = cache.ExistingR2Config(
            bucket="creative-asset-video-cache",
            endpoint="https://example.r2.cloudflarestorage.com",
            access_key="test", secret_key="test",
            signing_secret="Test-Secret-For-Unit-Only-2026-XYZ!" * 2,
        )
        self.memory = ThumbnailMemoryCache(
            max_bytes=512 * 1024, max_item_bytes=128 * 1024,
            ttl_seconds=90,
        )

    def identity(self, **overrides):
        args = dict(tenant_id="boostgame", pack_id="sp-" + "b" * 40,
                    slot=11, quality="preview", sha256="c" * 64)
        args.update(overrides)
        return args

    def test_13_roles_are_supported_but_originals_remain_forbidden(self):
        for slot in range(1, 14):
            key = cache.object_key(self.config, **self.identity(slot=slot))
            self.assertIn(f"/{slot:02d}/preview/", key)
        for invalid in (0, 14, 15, -1, True, "11"):
            with self.assertRaises(ValueError):
                cache.object_key(self.config, **self.identity(slot=invalid))
        for quality in ("original", "html", ""):
            with self.assertRaises(ValueError):
                cache.object_key(self.config, **self.identity(quality=quality))

    def test_hot_r2_bytes_reused_in_memory_only_after_authorized_call(self):
        reads = []
        value = b"verified-webp-content"
        def get_object(_config, key, kind):
            reads.append((key, kind))
            return value

        async def run():
            args = self.identity()
            result = await cache.read_derivative(self.config, **args)
            self.assertEqual(result, value)
            self.assertEqual(len(reads), 1)
            self.assertEqual(await cache.read_derivative(self.config, **args), value)
            self.assertEqual(len(reads), 1)
            self.assertEqual(await cache.read_derivative(
                self.config, **self.identity(tenant_id="another")), value)
            self.assertEqual(len(reads), 2)
        with (
            patch.object(cache, "_DERIVATIVE_MEMORY", self.memory),
            patch.object(cache, "_read", side_effect=get_object),
        ):
            asyncio.run(run())

    def test_negative_r2_read_is_never_kept_as_image(self):
        counter = []
        def get_object(_config, key, kind):
            counter.append(key)
            return None

        async def run():
            args = self.identity()
            self.assertIsNone(await cache.read_derivative(self.config, **args))
            self.assertIsNone(self.memory.get((args["tenant_id"], "missing", "preview")))
            self.assertIsNone(await cache.read_derivative(self.config, **args))
            self.assertEqual(len(counter), 1)  # bounded negative R2 cache
        with (
            patch.object(cache, "_DERIVATIVE_MEMORY", self.memory),
            patch.object(cache, "_read", side_effect=get_object),
        ):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()