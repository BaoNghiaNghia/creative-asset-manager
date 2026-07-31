from pathlib import Path

_IMAGE_MIME_BY_EXTENSION = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

def infer_media_type(filename: str | None, declared: str | None = None, upstream: str | None = None) -> str:
    for value in (declared, upstream):
        normalized = (value or "").split(";", 1)[0].strip().lower()
        if normalized.startswith(("image/", "video/")):
            return normalized
    extension_type = _IMAGE_MIME_BY_EXTENSION.get(Path(filename or "").suffix.lower())
    if extension_type:
        return extension_type
    return (declared or upstream or "application/octet-stream").split(";", 1)[0].strip().lower()

def is_previewable_media(filename: str | None, mime_type: str | None) -> bool:
    return infer_media_type(filename, mime_type).startswith(("image/", "video/"))
