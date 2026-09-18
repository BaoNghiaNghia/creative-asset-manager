from pathlib import Path

_IMAGE_MIME_BY_EXTENSION = {
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

_VIDEO_MIME_BY_EXTENSION = {
    ".avi": "video/x-msvideo",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".webm": "video/webm",
}

def infer_media_type(filename: str | None, declared: str | None = None, upstream: str | None = None) -> str:
    extension = Path(filename or "").suffix.lower()
    extension_type = _IMAGE_MIME_BY_EXTENSION.get(extension) or _VIDEO_MIME_BY_EXTENSION.get(extension)
    if extension_type == "image/avif":
        return extension_type
    for value in (declared, upstream):
        normalized = (value or "").split(";", 1)[0].strip().lower()
        if normalized.startswith(("image/", "video/")):
            return normalized
    if extension_type:
        return extension_type
    return (declared or upstream or "application/octet-stream").split(";", 1)[0].strip().lower()

def is_previewable_media(filename: str | None, mime_type: str | None) -> bool:
    return infer_media_type(filename, mime_type).startswith(("image/", "video/"))
