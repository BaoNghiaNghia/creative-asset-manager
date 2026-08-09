from __future__ import annotations

SUPPORTED_INVENTORY_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/avif"}
)
GOOGLE_DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def normalize_inventory_mime_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def is_supported_inventory_image(value: str | None) -> bool:
    return normalize_inventory_mime_type(value) in SUPPORTED_INVENTORY_IMAGE_MIME_TYPES


def is_google_drive_folder(value: str | None) -> bool:
    return normalize_inventory_mime_type(value) == GOOGLE_DRIVE_FOLDER_MIME_TYPE
