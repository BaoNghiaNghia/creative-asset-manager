from __future__ import annotations

import asyncio
import ipaddress
import socket
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image

ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class SafeImageDownloadError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def _validate_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise SafeImageDownloadError("provider_result_url_rejected", "Provider result URL is not allowed.")
    if parsed.port not in (None, 443):
        raise SafeImageDownloadError("provider_result_url_rejected", "Provider result URL is not allowed.")
    if not any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts):
        raise SafeImageDownloadError("provider_result_url_rejected", "Provider result host is not allowed.")
    loop = asyncio.get_running_loop()
    try:
        records = await loop.run_in_executor(None, socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise SafeImageDownloadError("provider_result_dns_failed", "Provider result host could not be resolved.", retryable=True) from exc
    if not records or any(not _is_public_address(record[4][0]) for record in records):
        raise SafeImageDownloadError("provider_result_url_rejected", "Provider result address is not public.")


def validate_image_bytes(data: bytes, declared_mime: str, target_size: int) -> str:
    mime = declared_mime.split(";", 1)[0].strip().lower()
    if mime not in ALLOWED_IMAGE_MIME_TYPES or len(data) > MAX_IMAGE_BYTES:
        raise SafeImageDownloadError("provider_result_invalid_image", "Provider returned an unsupported image.")
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            actual_mime = Image.MIME.get(image.format or "", "").lower()
            width, height = image.size
    except Exception as exc:
        raise SafeImageDownloadError("provider_result_invalid_image", "Provider returned an invalid image.") from exc
    if actual_mime != mime or (width, height) != (target_size, target_size):
        raise SafeImageDownloadError("provider_result_invalid_image", "Provider returned an image with invalid format or dimensions.")
    return mime


async def download_validated_image(
    client: httpx.AsyncClient,
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    target_size: int,
    max_redirects: int = 3,
) -> tuple[bytes, str]:
    current = url
    for redirect_count in range(max_redirects + 1):
        await _validate_url(current, allowed_hosts)
        try:
            async with client.stream("GET", current, follow_redirects=False, timeout=httpx.Timeout(30.0)) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count == max_redirects or not response.headers.get("location"):
                        raise SafeImageDownloadError("provider_result_redirect_rejected", "Provider result redirected too many times.")
                    current = urljoin(current, response.headers["location"])
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    raise SafeImageDownloadError("provider_result_unavailable", "Provider result is temporarily unavailable.", retryable=True)
                if response.is_error:
                    raise SafeImageDownloadError("provider_result_download_failed", "Provider result could not be downloaded.")
                declared_length = response.headers.get("content-length")
                if declared_length and int(declared_length) > MAX_IMAGE_BYTES:
                    raise SafeImageDownloadError("provider_result_too_large", "Provider result exceeds the size limit.")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise SafeImageDownloadError("provider_result_too_large", "Provider result exceeds the size limit.")
                    chunks.append(chunk)
                data = b"".join(chunks)
                mime = validate_image_bytes(data, response.headers.get("content-type", ""), target_size)
                return data, mime
        except httpx.HTTPError as exc:
            raise SafeImageDownloadError("provider_result_unavailable", "Provider result is temporarily unavailable.", retryable=True) from exc
    raise SafeImageDownloadError("provider_result_redirect_rejected", "Provider result redirected too many times.")
