from __future__ import annotations

from functools import lru_cache

from PIL import features


@lru_cache(maxsize=1)
def register_heif_decoder() -> bool:
    """Register pillow-heif once for both API and native worker processes."""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener(thumbnails=False)
    except (ImportError, OSError, RuntimeError):
        return False
    return True


def image_decoder_capabilities() -> dict[str, bool]:
    return {
        "avif": bool(features.check("avif")),
        "heic": register_heif_decoder(),
        "heif": register_heif_decoder(),
        "libvips": _load_vips() is not None,
    }


def _load_vips():
    try:
        import pyvips
        return pyvips
    except (ImportError, OSError):
        return None
