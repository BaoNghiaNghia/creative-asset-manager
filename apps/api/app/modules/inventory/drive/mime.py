from __future__ import annotations

from app.modules.pipeline.mime_types import (
    SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES,
    normalize_source_mime_type,
)

SUPPORTED_INVENTORY_IMAGE_MIME_TYPES = SUPPORTED_GOOGLE_DRIVE_IMAGE_MIME_TYPES
GOOGLE_DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def normalize_inventory_mime_type(value: str | None) -> str:
    return normalize_source_mime_type((value or "").split(";", 1)[0])


def is_supported_inventory_image(value: str | None) -> bool:
    return normalize_inventory_mime_type(value) in SUPPORTED_INVENTORY_IMAGE_MIME_TYPES


def is_google_drive_folder(value: str | None) -> bool:
    return normalize_inventory_mime_type(value) == GOOGLE_DRIVE_FOLDER_MIME_TYPE
