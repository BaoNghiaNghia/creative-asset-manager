"""Explicit opt-in, tiny dedicated-prefix R2 connectivity check."""
from __future__ import annotations
import asyncio
import hashlib
import os
from uuid import uuid4
from app.core.config import Settings
from app.providers.cloudflare.r2 import R2Adapter, R2NotFound


async def smoke(settings: Settings, adapter: R2Adapter | None = None) -> bool:
    if not settings.R2_VIDEO_CACHE_REAL_SMOKE or not settings.R2_VIDEO_CACHE_ENABLED:
        raise ValueError("Real R2 smoke requires both explicit opt-in flags")
    provider = adapter or R2Adapter(settings)
    key = f"video-cache-smoke-test/{uuid4().hex}"
    payload = b"CAM R2 smoke " + os.urandom(32)
    uploaded = False
    try:
        await provider._call("put_object", Key=key, Body=payload, ContentType="application/octet-stream")
        uploaded = True
        head = await provider.head_object(key)
        if head.size_bytes != len(payload):
            raise ValueError("R2 smoke HEAD size mismatch")
        response = await provider._call("get_object", Key=key)
        body = response["Body"]
        try:
            received = await asyncio.to_thread(body.read, len(payload) + 1)
        finally:
            body.close()
        if hashlib.sha256(received).digest() != hashlib.sha256(payload).digest():
            raise ValueError("R2 smoke GET bytes mismatch")
    finally:
        if uploaded:
            await provider.delete_object(key)
    try:
        await provider.head_object(key)
    except R2NotFound:
        return True
    raise ValueError("R2 smoke object remains after DELETE")


def main() -> int:
    settings = Settings()
    if not settings.R2_VIDEO_CACHE_REAL_SMOKE:
        raise SystemExit("Skipped: set R2_VIDEO_CACHE_REAL_SMOKE=true explicitly")
    if asyncio.run(smoke(settings)):
        print("R2 smoke passed: PUT, HEAD, GET, DELETE, missing HEAD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
