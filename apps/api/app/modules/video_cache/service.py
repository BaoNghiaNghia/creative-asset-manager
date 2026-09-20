"""Bounded, byte-exact upload of an authoritative original video to private R2."""
import asyncio
import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.providers.cloudflare.r2 import R2Adapter

PART_SIZE = 16 * 1024 * 1024  # Above S3/R2 5 MiB non-final-part minimum.
_HASH_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_TENANT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}", re.ASCII)


class VideoCacheIntegrityError(ValueError):
    pass


def video_cache_key(tenant_id: str, content_hash: str) -> str:
    if not _TENANT_RE.fullmatch(tenant_id) or not _HASH_RE.fullmatch(content_hash):
        raise ValueError("Invalid video cache identity")
    return f"video-cache/{tenant_id}/{content_hash}/original"


def validate_video_mime(mime_type: str) -> None:
    if not isinstance(mime_type, str) or not re.fullmatch(
        r"video/[A-Za-z0-9][A-Za-z0-9.+_-]*", mime_type, re.ASCII
    ):
        raise ValueError("Only video MIME types are cacheable")


@dataclass(frozen=True)
class VideoUploadResult:
    r2_key: str
    size_bytes: int
    content_hash: str
    etag: str | None


class VideoCacheService:
    def __init__(self, provider: R2Adapter, *, max_object_bytes: int, part_size: int = PART_SIZE):
        if max_object_bytes <= 0 or part_size < 5 * 1024 * 1024:
            raise ValueError("Invalid video upload limits")
        self.provider = provider
        self.max_object_bytes = max_object_bytes
        self.part_size = part_size

    async def upload_original(
        self,
        source: AsyncIterator[bytes],
        *,
        tenant_id: str,
        content_hash_expected: str,
        expected_size_bytes: int | None,
        mime_type: str,
        target_key: str | None = None,
        on_upload_started: Callable[[str], Awaitable[None]] | None = None,
    ) -> VideoUploadResult:
        key = video_cache_key(tenant_id, content_hash_expected)
        if target_key is not None and target_key != key:
            raise ValueError("Video cache key must be server generated")
        validate_video_mime(mime_type)
        if expected_size_bytes is not None and (
            expected_size_bytes <= 0 or expected_size_bytes > self.max_object_bytes
        ):
            raise ValueError("Expected video size is outside cache limits")

        upload_id = await self.provider.create_multipart_upload(key, mime_type)
        parts: list[dict] = []
        part_number = 1
        digest = hashlib.sha256()
        actual_size = 0
        buffer = bytearray()
        completed = False
        try:
            if on_upload_started is not None:
                await on_upload_started(upload_id)
            async for chunk in source:
                if not isinstance(chunk, bytes):
                    raise TypeError("Original stream must yield bytes")
                actual_size += len(chunk)
                if actual_size > self.max_object_bytes:
                    raise VideoCacheIntegrityError("Video exceeds cache object limit")
                digest.update(chunk)
                offset = 0
                while offset < len(chunk):
                    take = min(self.part_size - len(buffer), len(chunk) - offset)
                    buffer.extend(memoryview(chunk)[offset:offset + take])
                    offset += take
                    if len(buffer) == self.part_size:
                        etag = await self.provider.upload_part(key, upload_id, part_number, bytes(buffer))
                        parts.append({"ETag": etag, "PartNumber": part_number})
                        part_number += 1
                        buffer.clear()
            if actual_size == 0:
                raise VideoCacheIntegrityError("Original video is empty")
            if digest.hexdigest() != content_hash_expected:
                raise VideoCacheIntegrityError("Original video SHA-256 mismatch")
            if expected_size_bytes is not None and actual_size != expected_size_bytes:
                raise VideoCacheIntegrityError("Original video byte count mismatch")
            if buffer:
                etag = await self.provider.upload_part(key, upload_id, part_number, bytes(buffer))
                parts.append({"ETag": etag, "PartNumber": part_number})
            etag = await self.provider.complete_multipart_upload(key, upload_id, parts)
            completed = True
            head = await self.provider.head_object(key)
            if head.size_bytes != actual_size:
                raise VideoCacheIntegrityError("R2 object byte count mismatch")
            return VideoUploadResult(key, actual_size, digest.hexdigest(), etag or head.etag)
        except BaseException:
            try:
                if completed:
                    await asyncio.shield(self.provider.delete_object(key))
                else:
                    await asyncio.shield(self.provider.abort_multipart_upload(key, upload_id))
            except Exception:
                pass  # Keep original failure; retry/reconciliation is a later phase.
            raise
