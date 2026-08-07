from __future__ import annotations

import io
import threading
from collections import OrderedDict

from PIL import Image, ImageOps, features

AVIF_DECODER_AVAILABLE = features.check("avif")
_PREVIEW_CACHE_MAX_ITEMS = 128
_cache: OrderedDict[tuple[str, str, str, str], bytes] = OrderedDict()
_cache_lock = threading.Lock()


class PreviewConversionError(RuntimeError):
    pass


def preview_cache_get(key: tuple[str, str, str, str]) -> bytes | None:
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def preview_cache_put(key: tuple[str, str, str, str], value: bytes) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _PREVIEW_CACHE_MAX_ITEMS:
            _cache.popitem(last=False)


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
