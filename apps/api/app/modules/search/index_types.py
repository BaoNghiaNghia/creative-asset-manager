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

    async def switch_aliases(self, target_index: str) -> AliasSwitchResult: ...

    async def rollback_aliases(self, previous_index: str) -> AliasSwitchResult: ...

    async def search(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...
