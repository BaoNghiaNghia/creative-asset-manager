from __future__ import annotations

import io

from PIL import Image, ImageOps, features

from app.common.cache import ByteSizeTTLCache, CacheMetrics

AVIF_DECODER_AVAILABLE = features.check("avif")
PREVIEW_CACHE_VERSION = "v2"
PreviewCacheKey = tuple[str, str, str, str, str]

_preview_cache: ByteSizeTTLCache[PreviewCacheKey, bytes] = ByteSizeTTLCache(
    max_entries=2048,
    max_bytes=256 * 1024 * 1024,
    ttl_seconds=3600,
    size_of=len,
)


class PreviewConversionError(RuntimeError):
    pass


def preview_cache_get(key: PreviewCacheKey) -> bytes | None:
    return _preview_cache.get(key)


def preview_cache_put(key: PreviewCacheKey, value: bytes) -> None:
    _preview_cache.put(key, value)


def preview_cache_invalidate(
    *, tenant_id: str, external_source_id: str, item_id: str | None = None
) -> int:
    return _preview_cache.invalidate_where(
        lambda key: key[1] == tenant_id
        and key[2] == external_source_id
        and (item_id is None or key[3] == item_id)
    )


def preview_cache_clear() -> None:
    _preview_cache.clear()


def preview_cache_total_bytes() -> int:
    return _preview_cache.total_bytes


def preview_cache_metrics() -> CacheMetrics:
    return _preview_cache.metrics()


def convert_avif_to_webp(content: bytes) -> bytes:
    if not AVIF_DECODER_AVAILABLE:
        raise PreviewConversionError("AVIF preview decoding is unavailable in this Pillow build.")
    try:
        with Image.open(io.BytesIO(content)) as opened:
            opened.seek(0)
            image = ImageOps.exif_transpose(opened)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=82, method=6)
            return output.getvalue()
    except Exception as exc:
        raise PreviewConversionError("The AVIF image is malformed or could not be converted.") from exc
