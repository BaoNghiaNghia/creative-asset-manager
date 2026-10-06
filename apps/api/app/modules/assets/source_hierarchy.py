from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any


def _encode_provider_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def _onedrive_item_id(drive_id: str, item_id: str) -> str:
    return f"od:item:{_encode_provider_id(drive_id)}:{_encode_provider_id(item_id)}"


def source_parent_external_ids(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return normalized external parent IDs across source-provider metadata shapes.

    Google Drive and newer source rows expose `parents` or `parent_id`.
    Older OneDrive rows store Graph-native `parent_drive_id` + `parent_item_id`,
    while the synchronized source tree uses encoded `od:item:...` IDs. Normalize
    that legacy shape here so authorization/search can traverse existing rows
    without waiting for a destructive data migration or full re-sync.
    """
    values: Mapping[str, Any] = metadata if isinstance(metadata, Mapping) else {}

    raw = values.get("parents")
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = [value for value in raw if isinstance(value, str)]
    else:
        parent = values.get("parent_id")
        candidates = [parent] if isinstance(parent, str) else []

    normalized: list[str] = []
    for value in candidates:
        item = value.strip()
        if item and item not in normalized:
            normalized.append(item)
    if normalized:
        return tuple(normalized)

    parent_item_id = values.get("parent_item_id")
    parent_drive_id = values.get("parent_drive_id") or values.get("drive_id")
    if isinstance(parent_item_id, str) and isinstance(parent_drive_id, str):
        item = parent_item_id.strip()
        drive = parent_drive_id.strip()
        if item and drive:
            return (_onedrive_item_id(drive, item),)

    return ()
