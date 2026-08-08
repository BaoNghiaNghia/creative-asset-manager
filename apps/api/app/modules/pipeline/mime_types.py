from __future__ import annotations


class UnsupportedSourceMimeType(ValueError):
    pass


class SourceContentTooLarge(ValueError):
    pass


SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/avif"}
)

IGNORED_IMAGE_ANALYSIS_MIME_TYPES = frozenset({
    "image/x-photoshop", "image/vnd.adobe.photoshop",
    "image/dng", "image/x-adobe-dng",
})


def is_supported_google_drive_image_mime_type(mime_type: str | None) -> bool:
    return is_supported_image_mime_type(mime_type)


def normalize_source_mime_type(mime_type: str | None) -> str:
    return mime_type.strip().lower() if isinstance(mime_type, str) else ""

def is_supported_image_mime_type(mime_type: str | None) -> bool:
    return normalize_source_mime_type(mime_type) in SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES

def is_eligible_image_source_asset(source_asset) -> bool:
    return is_supported_image_mime_type(getattr(source_asset, "mime_type", None)) and getattr(source_asset, "deleted_at", None) is None


def is_ignored_image_analysis_mime_type(mime_type: str | None) -> bool:
    return normalize_source_mime_type(mime_type) in IGNORED_IMAGE_ANALYSIS_MIME_TYPES
