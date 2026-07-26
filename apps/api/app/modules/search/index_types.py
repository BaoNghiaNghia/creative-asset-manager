from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class SearchIndexDocument:
    asset_id: str
    tenant_id: str
    filename: str
    folder_path: str
    search_text: str
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
            "filename": self.filename,
            "folder_path": self.folder_path,
            "search_text": self.search_text,
            "search_terms": list(self.search_terms),
            "normalized_terms": list(self.normalized_terms),
            "phrases": list(self.phrases),
            "numbers": list(self.numbers),
            "facets": {
                name: list(values) for name, values in sorted(self.facets.items())
            },
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

    async def bulk_upsert_to_index(
        self, documents: Sequence[SearchIndexDocument], target_index: str
    ) -> int: ...

    async def switch_aliases(self, target_index: str) -> AliasSwitchResult: ...

    async def rollback_aliases(self, previous_index: str) -> AliasSwitchResult: ...

    async def search(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...

def build_search_index_document(analysis: Any, *, source_id: str = "", filename: str = "", folder_path: str = "") -> SearchIndexDocument:
    """Build the stable V2/V3 projection from one completed analysis."""
    projection = analysis.search_projection
    if not isinstance(projection, Mapping):
        raise ValueError("analysis has no persisted search projection")
    metadata = analysis.metadata_json if isinstance(analysis.metadata_json, Mapping) else {}
    visible: list[str] = []
    for entry in metadata.get("visible_text", ()) if isinstance(metadata.get("visible_text"), list) else ():
        if isinstance(entry, Mapping):
            for key in ("text", "normalized"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip() and value.strip() not in visible:
                    visible.append(value.strip())
    projection_text = str(projection.get("search_text") or "")
    search_text = " ".join(value for value in (filename, *visible, projection_text) if value)
    facets = projection.get("facets") or {}
    return SearchIndexDocument(
        asset_id=analysis.asset_id, tenant_id=analysis.tenant_id, source_id=source_id,
        filename=filename, folder_path=folder_path, visible_text=tuple(visible),
        search_suggest=search_text, search_text=search_text,
        search_terms=tuple(projection.get("search_terms") or ()),
        normalized_terms=tuple(projection.get("normalized_terms") or ()),
        phrases=tuple(projection.get("phrases") or ()), numbers=tuple(projection.get("numbers") or ()),
        facets={key: tuple(value) for key, value in facets.items()},
        path_values=tuple(projection.get("path_values") or ()),
        metadata_profile=analysis.metadata_profile,
        metadata_profile_version=analysis.metadata_profile_version,
        search_projection_version=analysis.search_projection_version or "",
    )