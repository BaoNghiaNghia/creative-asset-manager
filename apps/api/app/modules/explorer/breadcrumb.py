from __future__ import annotations

import logging
import time
from collections import OrderedDict
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
            logger.warning("Folder breadcrumb cycle_detected item_id=%s missing_parent_id=%s depth=%s", item_id, current, len(chain))
            return []
        visited.add(current)
        row = folders.get(current)
        if row is None:
            logger.warning("Folder breadcrumb missing_parent item_id=%s missing_parent_id=%s depth=%s", item_id, current, len(chain))
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
        logger.warning("Folder breadcrumb root_not_reached item_id=%s depth=%s", item_id, len(chain))
        return []
    if not chain or (source_root_id and chain[-1]["id"] != source_root_id and not (permitted_root_ids and chain[-1]["id"] in permitted_root_ids)):
        return []
    chain.reverse()
    return chain


class LocationBreadcrumbCache:
    def __init__(self, max_entries: int = 4096, ttl_seconds: float = 300):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[tuple[str, str, str], tuple[float, list[dict[str, str]]]] = OrderedDict()

    def get(self, key: tuple[str, str, str]) -> list[dict[str, str]] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires <= time.monotonic():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return [dict(item) for item in value]

    def put(self, key: tuple[str, str, str], value: list[dict[str, str]]) -> None:
        self._entries[key] = (time.monotonic() + self.ttl_seconds, [dict(item) for item in value])
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, *, tenant_id: str, external_source_id: str, item_id: str | None = None) -> None:
        for key in list(self._entries):
            if key[:2] == (tenant_id, external_source_id) and (item_id is None or key[2] == item_id):
                self._entries.pop(key, None)


location_breadcrumb_cache = LocationBreadcrumbCache()
