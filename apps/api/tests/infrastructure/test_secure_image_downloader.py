import hashlib
import io
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from app.infrastructure.downloader.secure_image import (
    DownloadLimitError,
    DownloaderConfig,
    InvalidImageError,
    SecureImageDownloader,
    UnsafeUrlError,
    redact_url,
)


def image_bytes(size=(2, 2), image_format="PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (20, 40, 60)).save(output, format=image_format)
    return output.getvalue()


class SecureImageDownloaderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.directory.name)
        self.resolved: list[str] = []

    def tearDown(self) -> None:
        self.directory.cleanup()

    async def resolver(self, hostname: str) -> tuple[str, ...]:
        self.resolved.append(hostname)
        return ("93.184.216.34",)

    def downloader(self, handler, **overrides) -> SecureImageDownloader:
        values = {
            "hostname_allowlist": ("assets.example.com",),
            "max_response_bytes": 1024 * 1024,
            "max_width": 100,
            "max_height": 100,
            "max_pixels": 10_000,
        }
        values.update(overrides)
        return SecureImageDownloader(
            DownloaderConfig(**values),
            enabled=True,
            dns_resolver=self.resolver,
            transport=httpx.MockTransport(handler),
            temp_directory=self.temp_path,
        )

    async def test_streams_hashes_decodes_and_cleans_temp_file(self) -> None:
        payload = image_bytes()

        def handler(_request):
            self.assertEqual(_request.url.host, "93.184.216.34")
            self.assertEqual(_request.headers["host"], "assets.example.com")
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=payload)

        downloader = self.downloader(handler)
        async with downloader.download(
            "https://assets.example.com/image.png?signature=secret"
        ) as result:
            self.assertTrue(result.path.exists())
            self.assertEqual(result.content_hash, hashlib.sha256(payload).hexdigest())
            self.assertEqual((result.width, result.height), (2, 2))
            self.assertEqual(result.image_format, "PNG")
            self.assertNotIn("signature", result.source_url)
            path = result.path
        self.assertFalse(path.exists())
        self.assertEqual(list(self.temp_path.iterdir()), [])

    async def test_feature_flag_blocks_download_before_network(self) -> None:
        calls = []
        downloader = SecureImageDownloader(
            DownloaderConfig(("assets.example.com",)),
            enabled=False,
            transport=httpx.MockTransport(lambda request: calls.append(request)),
            temp_directory=self.temp_path,
        )
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            async with downloader.download("https://assets.example.com/image.png"):
                pass
        self.assertEqual(calls, [])

    async def test_rejects_http_credentials_and_non_allowlisted_hosts(self) -> None:
        downloader = self.downloader(lambda _request: httpx.Response(500))
        for url in (
            "http://assets.example.com/image.png",
            "https://user:password@assets.example.com/image.png",
            "https://assets.example.com.evil.test/image.png",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafeUrlError):
                    async with downloader.download(url):
                        pass

    async def test_blocks_private_loopback_link_local_and_metadata_dns(self) -> None:
        blocked = (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.10.1",
            "169.254.169.254",
            "100.100.100.200",
            "::1",
        )
        for address in blocked:
            async def resolver(_hostname, value=address):
                return (value,)

            downloader = SecureImageDownloader(
                DownloaderConfig(("assets.example.com",)),
                enabled=True,
                dns_resolver=resolver,
                transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
                temp_directory=self.temp_path,
            )
            with self.subTest(address=address), self.assertRaises(UnsafeUrlError):
                async with downloader.download("https://assets.example.com/image.png"):
                    pass
        self.assertEqual(list(self.temp_path.iterdir()), [])

    async def test_revalidates_redirect_hostname_and_dns(self) -> None:
        payload = image_bytes()

        def handler(request):
            if request.headers["host"] == "assets.example.com":
                return httpx.Response(
                    302, headers={"location": "https://cdn.example.com/final.png"}
                )
            return httpx.Response(200, content=payload)

        downloader = self.downloader(
            handler, hostname_allowlist=("assets.example.com", "cdn.example.com")
        )
        async with downloader.download("https://assets.example.com/start") as result:
            self.assertEqual(result.image_format, "PNG")
        self.assertEqual(self.resolved, ["assets.example.com", "cdn.example.com"])

    async def test_redirect_to_non_allowlisted_host_is_blocked(self) -> None:
        downloader = self.downloader(
            lambda _request: httpx.Response(
                302, headers={"location": "https://evil.example.net/image.png"}
            )
        )
        with self.assertRaises(UnsafeUrlError):
            async with downloader.download("https://assets.example.com/start"):
                pass

    async def test_max_redirects_is_enforced(self) -> None:
        downloader = self.downloader(
            lambda _request: httpx.Response(302, headers={"location": "/again"}),
            max_redirects=1,
        )
        with self.assertRaisesRegex(Exception, "maximum redirects"):
            async with downloader.download("https://assets.example.com/start"):
                pass

    async def test_byte_limit_rejects_declared_and_streamed_oversize(self) -> None:
        payload = image_bytes()
        for handler in (
            lambda _request: httpx.Response(
                200, headers={"content-length": "9999"}, content=payload
            ),
            lambda _request: httpx.Response(200, content=payload),
        ):
            downloader = self.downloader(handler, max_response_bytes=10)
            with self.assertRaises(DownloadLimitError):
                async with downloader.download("https://assets.example.com/image.png"):
                    pass
        self.assertEqual(list(self.temp_path.iterdir()), [])

    async def test_pixel_and_dimension_limits_are_enforced_before_decode(self) -> None:
        payload = image_bytes((11, 10))
        downloader = self.downloader(
            lambda _request: httpx.Response(200, content=payload),
            max_width=10,
            max_height=10,
            max_pixels=100,
        )
        with self.assertRaises(DownloadLimitError):
            async with downloader.download("https://assets.example.com/image.png"):
                pass

    async def test_invalid_magic_and_truncated_image_are_rejected(self) -> None:
        payloads = (b"not an image", b"\x89PNG\r\n\x1a\ntruncated")
        for payload in payloads:
            downloader = self.downloader(
                lambda _request, value=payload: httpx.Response(200, content=value)
            )
            with self.subTest(payload=payload), self.assertRaises(InvalidImageError):
                async with downloader.download("https://assets.example.com/image.png"):
                    pass

    async def test_timeout_cleans_temporary_file(self) -> None:
        def handler(request):
            raise httpx.ReadTimeout("slow response", request=request)

        downloader = self.downloader(handler)
        with self.assertRaises(httpx.ReadTimeout):
            async with downloader.download("https://assets.example.com/image.png"):
                pass
        self.assertEqual(list(self.temp_path.iterdir()), [])

    def test_url_redaction_removes_credentials_query_and_fragment(self) -> None:
        safe = redact_url(
            "https://user:password@assets.example.com/path/image.png?token=secret#fragment"
        )
        self.assertEqual(safe, "https://assets.example.com/path/image.png")
