from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from app.modules.ai_metadata.normalizer import MetadataNormalizer
from app.modules.ai_metadata.traverser import MetadataTraverser


@dataclass(frozen=True, slots=True)
class SearchIndexDocument:
    asset_id: str
    tenant_id: str
    filename: str
    folder_path: str
    search_text: str
    source_id: str = ""
    parent_id: str = ""
    # Includes this item. A scope folder therefore matches both itself and all descendants.
    ancestor_ids: tuple[str, ...] = ()
    visible_text: tuple[str, ...] = ()
    search_suggest: str = ""
    search_terms: tuple[str, ...] = ()
    normalized_terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    facets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    path_values: tuple[Mapping[str, str], ...] = ()
    metadata_profile: str = ""
    metadata_profile_version: str = ""
    search_projection_version: str = ""

    def to_document(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "parent_id": self.parent_id,
            "ancestor_ids": list(self.ancestor_ids),
            "filename": self.filename,
            "folder_path": self.folder_path,
            "visible_text": list(self.visible_text),
            "search_suggest": self.search_suggest,
            "search_text": self.search_text,
            "search_terms": list(self.search_terms),
            "normalized_terms": list(self.normalized_terms),
            "phrases": list(self.phrases),
            "numbers": list(self.numbers),
            "facets": {name: list(values) for name, values in sorted(self.facets.items())},
            "path_values": [dict(item) for item in self.path_values],
            "metadata_profile": self.metadata_profile,
            "metadata_profile_version": self.metadata_profile_version,
            "search_projection_version": self.search_projection_version,
        }


@dataclass(frozen=True, slots=True)
class AliasSwitchResult:
    target_index: str
    previous_read_indices: tuple[str, ...]
    previous_write_indices: tuple[str, ...]


class SearchIndexProvider(Protocol):
    async def create_index(self, version: str) -> str: ...
    async def bulk_upsert(self, documents: Sequence[SearchIndexDocument]) -> int: ...
    async def bulk_upsert_to_index(self, documents: Sequence[SearchIndexDocument], target_index: str) -> int: ...
    async def switch_aliases(self, target_index: str) -> AliasSwitchResult: ...
    async def rollback_aliases(self, previous_index: str) -> AliasSwitchResult: ...
    async def search(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...
    async def open_point_in_time(self, *, keep_alive: str = "2m") -> str: ...
    async def search_with_pit(
        self, body: Mapping[str, Any], *, pit_id: str, keep_alive: str = "2m"
    ) -> Mapping[str, Any]: ...
    async def close_point_in_time(self, pit_id: str) -> bool: ...


def build_search_index_document(
    analysis: Any,
    *,
    source_id: str = "",
    parent_id: str = "",
    ancestor_ids: Sequence[str] = (),
    filename: str = "",
    folder_path: str = "",
) -> SearchIndexDocument:
    """Build the stable V2/V3 projection from one completed analysis."""
    projection = analysis.search_projection
    if not isinstance(projection, Mapping):
        raise ValueError("analysis has no persisted search projection")
    metadata = analysis.metadata_json if isinstance(analysis.metadata_json, Mapping) else {}
    visible = _visible_text_values(metadata)
    metadata_values = _searchable_metadata_values(metadata)
    projection_text = str(projection.get("search_text") or "")
    search_text = MetadataNormalizer.normalize_text(
        " ".join(_unique_nonempty((filename, *visible, *metadata_values, projection_text)))
    )
    facets = projection.get("facets") or {}
    return SearchIndexDocument(
        asset_id=analysis.asset_id,
        tenant_id=analysis.tenant_id,
        source_id=source_id,
        parent_id=parent_id,
        ancestor_ids=_unique_nonempty(ancestor_ids),
        filename=filename,
        folder_path=folder_path,
        visible_text=visible,
        search_suggest=search_text,
        search_text=search_text,
        search_terms=tuple(projection.get("search_terms") or ()),
        normalized_terms=tuple(projection.get("normalized_terms") or ()),
        phrases=tuple(projection.get("phrases") or ()),
        numbers=tuple(projection.get("numbers") or ()),
        facets={key: tuple(value) for key, value in facets.items()},
        path_values=tuple(projection.get("path_values") or ()),
        metadata_profile=analysis.metadata_profile,
        metadata_profile_version=analysis.metadata_profile_version,
        search_projection_version=analysis.search_projection_version or "",
    )


def _visible_text_values(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    entries = metadata.get("visible_text")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, Mapping):
                for key in ("text", "normalized"):
                    value = entry.get(key)
                    if isinstance(value, str):
                        values.append(value)
    return _unique_nonempty(values)


def _searchable_metadata_values(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Flatten safe scalar metadata for V3 full-text matching.

    MetadataTraverser applies shared sensitive-field and URL exclusions and
    intentionally preserves short OCR tokens such as BSN and RN.
    """
    return _unique_nonempty(
        item.original_value
        for item in MetadataTraverser().traverse(metadata)
        if item.value_type == "string"
    )


def _unique_nonempty(values: Any) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)
