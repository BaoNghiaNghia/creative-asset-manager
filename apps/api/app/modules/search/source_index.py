from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import AssetSourceLinkModel, SourceAssetModel


@dataclass(frozen=True, slots=True)
class SearchSourceIndexDetails:
    source_id: str = ""
    filename: str = ""
    folder_path: str = ""
    parent_id: str = ""
    ancestor_ids: tuple[str, ...] = ()


class SearchSourceIndexResolver:
    """Resolves source hierarchy once while documents are indexed, never at search time."""

    def __init__(self, session: Session):
        self.session = session
        self._parent_maps: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}

    def for_asset(self, *, tenant_id: str, asset_id: str) -> SearchSourceIndexDetails:
        source = self.session.scalar(
            select(SourceAssetModel)
            .join(AssetSourceLinkModel, AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                AssetSourceLinkModel.asset_id == asset_id,
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.deleted_at.is_(None),
            )
            .order_by(SourceAssetModel.id)
            .limit(1)
        )
        return self.for_source(source) if source is not None else SearchSourceIndexDetails()

    def for_source(self, source: SourceAssetModel) -> SearchSourceIndexDetails:
        metadata = source.source_metadata if isinstance(source.source_metadata, Mapping) else {}
        parent_ids = self._parent_ids(metadata)
        ancestors = self._ancestor_ids(source)
        path = metadata.get("path") or metadata.get("folder_path") or ""
        return SearchSourceIndexDetails(
            source_id=str(source.external_source_id),
            filename=str(source.filename or ""),
            folder_path=path if isinstance(path, str) else "",
            parent_id=parent_ids[0] if parent_ids else "",
            ancestor_ids=ancestors,
        )

    def _ancestor_ids(self, source: SourceAssetModel) -> tuple[str, ...]:
        own_id = str(source.external_asset_id or "").strip()
        if not own_id:
            return ()
        parents = self._parent_map(source.tenant_id, source.external_source_id)
        result: list[str] = [own_id]
        seen = {own_id}
        frontier = list(parents.get(own_id, ()))
        # Cycles and malformed provider metadata fail closed, while keeping a bounded document.
        while frontier and len(result) < 128:
            item = str(frontier.pop(0)).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
            frontier.extend(parents.get(item, ()))
        return tuple(result)

    def _parent_map(self, tenant_id: str, external_source_id: str) -> dict[str, tuple[str, ...]]:
        key = (tenant_id, external_source_id)
        cached = self._parent_maps.get(key)
        if cached is not None:
            return cached
        rows = self.session.execute(
            select(SourceAssetModel.external_asset_id, SourceAssetModel.source_metadata).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == external_source_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        ).all()
        mapping = {
            str(external_id): self._parent_ids(metadata if isinstance(metadata, Mapping) else {})
            for external_id, metadata in rows
            if str(external_id).strip()
        }
        self._parent_maps[key] = mapping
        return mapping

    @staticmethod
    def _parent_ids(metadata: Mapping[str, Any]) -> tuple[str, ...]:
        raw = metadata.get("parents")
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple)):
            values = [value for value in raw if isinstance(value, str)]
        else:
            parent = metadata.get("parent_id")
            values = [parent] if isinstance(parent, str) else []
        result: list[str] = []
        for value in values:
            normalized = value.strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return tuple(result)
