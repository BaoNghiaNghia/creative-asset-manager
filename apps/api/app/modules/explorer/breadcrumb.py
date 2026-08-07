from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)
MAX_BREADCRUMB_DEPTH = 64

def resolve_breadcrumb(
    *, item_id: str, parent_id: str | None, folders: Mapping[str, Mapping[str, object]],
    source_root_id: str | None = None, permitted_root_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Resolve folder-only ancestry without crossing a configured/viewer root."""
    current = str(parent_id or "")
    chain: list[dict[str, str]] = []
    visited: set[str] = set()
    while current and len(chain) < MAX_BREADCRUMB_DEPTH:
        if current in visited:
            logger.warning("Folder breadcrumb cycle for item_id=%s parent_id=%s", item_id, current)
            return []
        visited.add(current)
        row = folders.get(current)
        if row is None:
            logger.warning("Folder breadcrumb parent missing item_id=%s parent_id=%s", item_id, current)
            return []
        name = str(row.get("name") or "").strip()
        if not name:
            return []
        chain.append({"id": current, "name": name})
        if permitted_root_ids and current in permitted_root_ids:
            break
        if source_root_id and current == source_root_id:
            break
        current = str(row.get("parent_id") or "")
    else:
        logger.warning("Folder breadcrumb depth exceeded item_id=%s", item_id)
        return []
    if not chain or (source_root_id and chain[-1]["id"] != source_root_id and not (permitted_root_ids and chain[-1]["id"] in permitted_root_ids)):
        return []
    chain.reverse()
    return chain
