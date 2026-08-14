"""Recognition of Google Drive objects written by Creative Asset Manager.

Only CAM-owned appProperties are authoritative. Folder placement is not:
users are allowed to keep their own files in any Drive folder.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_MANAGED_BINARY_KEYS = (
    "cam_tenant_id",
    "cam_asset_id",
    "cam_content_hash",
)


def cam_app_properties(item: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return Drive appProperties from either provider or persisted metadata."""
    if not item:
        return {}
    properties = item.get("appProperties") or item.get("app_properties")
    return properties if isinstance(properties, Mapping) else {}


def is_cam_managed_file(item: Mapping[str, Any] | None) -> bool:
    """Whether a Drive item is a CAM staging object or metadata sidecar."""
    properties = cam_app_properties(item)
    if properties.get("cam_sidecar"):
        return True
    return all(str(properties.get(key) or "").strip() for key in _MANAGED_BINARY_KEYS)
