import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentHashResult:
    content_hash: str
    size_bytes: int


async def sha256_stream(chunks: AsyncIterator[bytes]) -> ContentHashResult:
    """Hash an async byte stream without retaining file content in memory."""
    digest = hashlib.sha256()
    size_bytes = 0
    async for chunk in chunks:
        if not isinstance(chunk, bytes):
            raise TypeError("content stream chunks must be bytes")
        if not chunk:
            continue
        digest.update(chunk)
        size_bytes += len(chunk)
    return ContentHashResult(digest.hexdigest(), size_bytes)
