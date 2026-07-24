from __future__ import annotations


class UnsupportedSourceMimeType(ValueError):
    pass


class SourceContentTooLarge(ValueError):
    pass


SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)


def is_supported_google_drive_image_mime_type(mime_type: str | None) -> bool:
    return (
        isinstance(mime_type, str)
        and mime_type.strip().lower() in SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES
    )
