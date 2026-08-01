from __future__ import annotations

from collections.abc import Iterable
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
        self._sources_by_asset: dict[tuple[str, str], SourceAssetModel | None] = {}
        self._details_by_source: dict[tuple[str, str, str], SearchSourceIndexDetails] = {}

    def preload_assets(self, *, tenant_id: str, asset_ids: Iterable[str]) -> None:
        """Load a bounded operation page of asset/source links in one query."""
        requested = tuple(dict.fromkeys(asset_id for asset_id in asset_ids if asset_id))
        uncached = tuple(
            asset_id
            for asset_id in requested
            if (tenant_id, asset_id) not in self._sources_by_asset
        )
        if not uncached:
            return
        for asset_id in uncached:
            self._sources_by_asset[(tenant_id, asset_id)] = None
        rows = self.session.execute(
            select(AssetSourceLinkModel.asset_id, SourceAssetModel)
            .join(SourceAssetModel, AssetSourceLinkModel.source_asset_id == SourceAssetModel.id)
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                AssetSourceLinkModel.asset_id.in_(uncached),
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.deleted_at.is_(None),
            )
            .order_by(AssetSourceLinkModel.asset_id, SourceAssetModel.id)
        ).all()
        # Keep the existing deterministic first-source behavior for an asset
        # that has more than one linked source record.
        for asset_id, source in rows:
            key = (tenant_id, str(asset_id))
            if self._sources_by_asset[key] is None:
                self._sources_by_asset[key] = source

    def for_asset(self, *, tenant_id: str, asset_id: str) -> SearchSourceIndexDetails:
        key = (tenant_id, asset_id)
        if key not in self._sources_by_asset:
            self.preload_assets(tenant_id=tenant_id, asset_ids=(asset_id,))
        source = self._sources_by_asset.get(key)
        return self.for_source(source) if source is not None else SearchSourceIndexDetails()

    def for_source(self, source: SourceAssetModel) -> SearchSourceIndexDetails:
        cache_key = (
            str(source.tenant_id),
            str(source.external_source_id),
            str(source.external_asset_id),
        )
        cached = self._details_by_source.get(cache_key)
        if cached is not None:
            return cached
        metadata = source.source_metadata if isinstance(source.source_metadata, Mapping) else {}
        parent_ids = self._parent_ids(metadata)
        ancestors = self._ancestor_ids(source)
        path = metadata.get("path") or metadata.get("folder_path") or ""
        details = SearchSourceIndexDetails(
            source_id=str(source.external_source_id),
            filename=str(source.filename or ""),
            folder_path=path if isinstance(path, str) else "",
            parent_id=parent_ids[0] if parent_ids else "",
            ancestor_ids=ancestors,
        )
        self._details_by_source[cache_key] = details
        return details

    def clear_page_cache(self) -> None:
        """Release per-page source metadata while retaining parent maps for this run."""
        self._sources_by_asset.clear()
        self._details_by_source.clear()

    def clear(self) -> None:
        self._parent_maps.clear()
        self.clear_page_cache()

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
