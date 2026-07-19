from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import os
import socket
import tempfile
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

DnsResolver = Callable[[str], Awaitable[tuple[str, ...]]]
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
CLOUD_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


class SecureDownloadError(RuntimeError):
    pass


class UnsafeUrlError(SecureDownloadError):
    pass


class DownloadLimitError(SecureDownloadError):
    pass


class InvalidImageError(SecureDownloadError):
    pass


@dataclass(frozen=True, slots=True)
class DownloaderConfig:
    hostname_allowlist: tuple[str, ...]
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 20.0
    max_redirects: int = 3
    max_response_bytes: int = 25 * 1024 * 1024
    max_width: int = 12000
    max_height: int = 12000
    max_pixels: int = 80_000_000
    chunk_size: int = 64 * 1024


@dataclass(frozen=True, slots=True)
class DownloadedImage:
    path: Path
    content_hash: str
    size_bytes: int
    width: int
    height: int
    image_format: str
    source_url: str


async def resolve_hostname(hostname: str) -> tuple[str, ...]:
    def resolve() -> tuple[str, ...]:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        return tuple(sorted({record[4][0] for record in records}))

    return await asyncio.to_thread(resolve)


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, "", ""))


def _signature_format(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "WEBP"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    if header.startswith(b"BM"):
        return "BMP"
    return None


class SecureImageDownloader:
    def __init__(
        self,
        config: DownloaderConfig,
        *,
        enabled: bool = False,
        dns_resolver: DnsResolver = resolve_hostname,
        transport: httpx.AsyncBaseTransport | None = None,
        temp_directory: str | Path | None = None,
    ):
        self.config = config
        self.enabled = enabled
        self.dns_resolver = dns_resolver
        self.transport = transport
        self.temp_directory = str(temp_directory) if temp_directory else None

    @asynccontextmanager
    async def download(self, url: str) -> AsyncIterator[DownloadedImage]:
        result: DownloadedImage | None = None
        try:
            result = await self._download(url)
            yield result
        finally:
            if result is not None:
                result.path.unlink(missing_ok=True)

    async def _download(self, url: str) -> DownloadedImage:
        if not self.enabled:
            raise RuntimeError("external asset downloader is disabled")
        current_url = url
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix="cam-download-", suffix=".image", dir=self.temp_directory
        )
        os.close(file_descriptor)
        temp_path = Path(temp_name)
        try:
            timeout = httpx.Timeout(
                self.config.read_timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            )
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=self.transport,
                trust_env=False,
            ) as client:
                for redirect_count in range(self.config.max_redirects + 1):
                    hostname, addresses = await self._validate_url(current_url)
                    request_url, host_header = self._pin_url(current_url, addresses[0])
                    request_extensions = {"sni_hostname": hostname}
                    request_headers = {"Host": host_header}
                    logger.info("Downloading external image from %s", redact_url(current_url))
                    async with client.stream(
                        "GET",
                        request_url,
                        headers=request_headers,
                        extensions=request_extensions,
                    ) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise SecureDownloadError("redirect response has no Location header")
                            if redirect_count >= self.config.max_redirects:
                                raise SecureDownloadError("maximum redirects exceeded")
                            current_url = urljoin(current_url, location)
                            continue
                        response.raise_for_status()
                        declared_size = response.headers.get("content-length")
                        if declared_size and int(declared_size) > self.config.max_response_bytes:
                            raise DownloadLimitError("response exceeds maximum byte limit")
                        digest = hashlib.sha256()
                        size = 0
                        signature_buffer = bytearray()
                        with temp_path.open("wb") as output:
                            async for chunk in response.aiter_bytes(self.config.chunk_size):
                                if not chunk:
                                    continue
                                size += len(chunk)
                                if size > self.config.max_response_bytes:
                                    raise DownloadLimitError("response exceeds maximum byte limit")
                                if len(signature_buffer) < 32:
                                    signature_buffer.extend(chunk[: 32 - len(signature_buffer)])
                                digest.update(chunk)
                                output.write(chunk)
                        expected_format = _signature_format(bytes(signature_buffer))
                        if expected_format is None:
                            raise InvalidImageError("unsupported or invalid image signature")
                        width, height, actual_format = self._validate_image(
                            temp_path, expected_format
                        )
                        return DownloadedImage(
                            path=temp_path,
                            content_hash=digest.hexdigest(),
                            size_bytes=size,
                            width=width,
                            height=height,
                            image_format=actual_format,
                            source_url=redact_url(current_url),
                        )
            raise SecureDownloadError("download did not produce a response")
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    async def _validate_url(self, value: str) -> tuple[str, tuple[str, ...]]:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https":
            raise UnsafeUrlError("only HTTPS URLs are allowed")
        if not parsed.hostname:
            raise UnsafeUrlError("URL hostname is required")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeUrlError("URL credentials are not allowed")
        hostname = parsed.hostname.rstrip(".").lower()
        allowlist = tuple(item.rstrip(".").lower() for item in self.config.hostname_allowlist)
        if not any(hostname == item or hostname.endswith(f".{item}") for item in allowlist):
            raise UnsafeUrlError("URL hostname is not allowlisted")
        try:
            addresses = (hostname,) if ipaddress.ip_address(hostname) else ()
        except ValueError:
            addresses = await self.dns_resolver(hostname)
        if not addresses:
            raise UnsafeUrlError("URL hostname did not resolve")
        validated: list[str] = []
        for raw_address in addresses:
            try:
                address = ipaddress.ip_address(raw_address)
            except ValueError as exc:
                raise UnsafeUrlError("DNS returned an invalid address") from exc
            if address in CLOUD_METADATA_ADDRESSES or not address.is_global:
                raise UnsafeUrlError("URL resolves to a non-public address")
            validated.append(str(address))
        return hostname, tuple(validated)

    @staticmethod
    def _pin_url(value: str, address: str) -> tuple[str, str]:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        port = parsed.port or 443
        ip = ipaddress.ip_address(address)
        network_host = f"[{ip}]" if ip.version == 6 else str(ip)
        if port != 443:
            network_host = f"{network_host}:{port}"
        host_header = hostname if port == 443 else f"{hostname}:{port}"
        return (
            urlunsplit((parsed.scheme, network_host, parsed.path, parsed.query, "")),
            host_header,
        )

    def _validate_image(self, path: Path, expected_format: str) -> tuple[int, int, str]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    actual_format = image.format or ""
                    width, height = image.size
                    self._validate_dimensions(width, height)
                    if actual_format != expected_format:
                        raise InvalidImageError("image signature and decoder format disagree")
                    image.verify()
                with Image.open(path) as decoded:
                    self._validate_dimensions(*decoded.size)
                    decoded.load()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning) as exc:
            raise InvalidImageError("image decode validation failed") from exc
        return width, height, actual_format

    def _validate_dimensions(self, width: int, height: int) -> None:
        if width > self.config.max_width or height > self.config.max_height:
            raise DownloadLimitError("image dimensions exceed configured limit")
        if width * height > self.config.max_pixels:
            raise DownloadLimitError("image pixel count exceeds configured limit")
