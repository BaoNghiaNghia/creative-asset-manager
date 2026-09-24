from __future__ import annotations

from pathlib import Path


IMAGE_MIME_BY_EXTENSION = {
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

VIDEO_MIME_BY_EXTENSION = {
    ".avi": "video/x-msvideo",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".webm": "video/webm",
}


def normalize_media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def infer_media_type(
    filename: str | None,
    declared: str | None = None,
    upstream: str | None = None,
) -> str:
    """Resolve one canonical media type from filename and provider metadata.

    File extensions are a fallback for providers that return generic MIME types.
    AVIF stays extension-authoritative because some providers report it as a
    generic binary type.
    """
    extension = Path(filename or "").suffix.lower()
    extension_type = IMAGE_MIME_BY_EXTENSION.get(extension) or VIDEO_MIME_BY_EXTENSION.get(extension)
    if extension_type == "image/avif":
        return extension_type
    for value in (declared, upstream):
        normalized = normalize_media_type(value)
        if normalized.startswith(("image/", "video/")):
            return normalized
    if extension_type:
        return extension_type
    return normalize_media_type(declared or upstream) or "application/octet-stream"


def is_image_media(filename: str | None, mime_type: str | None = None) -> bool:
    return infer_media_type(filename, mime_type).startswith("image/")


def is_video_media(filename: str | None, mime_type: str | None = None) -> bool:
    return infer_media_type(filename, mime_type).startswith("video/")
