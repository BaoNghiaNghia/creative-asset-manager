from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import (
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)


@dataclass(frozen=True, slots=True)
class SearchSourceIndexDetails:
    source_id: str = ""
    source_provider: str = ""
    filename: str = ""
    folder_path: str = ""
    parent_id: str = ""
    ancestor_ids: tuple[str, ...] = ()
    media_kind: str = ""
    mime_type: str = ""
    extension: str = ""
    source_created_at: str | None = None
    source_modified_at: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    file_size_bytes: int | None = None


class SearchSourceIndexResolver:
    """Resolves source hierarchy and typed source fields once at index time."""

    def __init__(self, session: Session):
        self.session = session
        self._parent_maps: dict[
            tuple[str, str], dict[str, tuple[str, ...]]
        ] = {}
        self._sources_by_asset: dict[
            tuple[str, str], tuple[SourceAssetModel, str] | None
        ] = {}
        self._details_by_source: dict[
            tuple[str, str, str], SearchSourceIndexDetails
        ] = {}

    def preload_assets(
        self, *, tenant_id: str, asset_ids: Iterable[str]
    ) -> None:
        requested = tuple(dict.fromkeys(
            asset_id for asset_id in asset_ids if asset_id
        ))
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
            select(
                AssetSourceLinkModel.asset_id,
                SourceAssetModel,
                ExternalSourceModel.source_type,
            )
            .join(
                SourceAssetModel,
                AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
            )
            .join(
                ExternalSourceModel,
                ExternalSourceModel.id == SourceAssetModel.external_source_id,
            )
            .where(
                AssetSourceLinkModel.tenant_id == tenant_id,
                AssetSourceLinkModel.asset_id.in_(uncached),
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.deleted_at.is_(None),
                ExternalSourceModel.tenant_id == tenant_id,
            )
            .order_by(AssetSourceLinkModel.asset_id, SourceAssetModel.id)
        ).all()
        # One document per internal asset. Preserve the existing deterministic
        # first-live-source invariant until the source-domain model is resolved.
        for asset_id, source, source_type in rows:
            key = (tenant_id, str(asset_id))
            if self._sources_by_asset[key] is None:
                self._sources_by_asset[key] = (source, str(source_type or ""))

    def for_asset(
        self, *, tenant_id: str, asset_id: str
    ) -> SearchSourceIndexDetails:
        key = (tenant_id, asset_id)
        if key not in self._sources_by_asset:
            self.preload_assets(tenant_id=tenant_id, asset_ids=(asset_id,))
        pair = self._sources_by_asset.get(key)
        if pair is None:
            return SearchSourceIndexDetails()
        source, source_type = pair
        return self.for_source(source, source_type=source_type)

    def for_source(
        self,
        source: SourceAssetModel,
        *,
        source_type: str = "",
    ) -> SearchSourceIndexDetails:
        cache_key = (
            str(source.tenant_id),
            str(source.external_source_id),
            str(source.external_asset_id),
        )
        cached = self._details_by_source.get(cache_key)
        if cached is not None:
            return cached
        metadata = (
            source.source_metadata
            if isinstance(source.source_metadata, Mapping)
            else {}
        )
        filename = str(getattr(source, "filename", "") or "")
        mime_type = str(getattr(source, "mime_type", "") or "").casefold()
        parent_ids = self._parent_ids(metadata)
        ancestors = self._ancestor_ids(source)
        path = metadata.get("path") or metadata.get("folder_path") or ""
        details = SearchSourceIndexDetails(
            source_id=str(source.external_source_id),
            source_provider=self._source_provider(source_type),
            filename=filename,
            folder_path=path if isinstance(path, str) else "",
            parent_id=parent_ids[0] if parent_ids else "",
            ancestor_ids=ancestors,
            media_kind=self._media_kind(mime_type),
            mime_type=mime_type,
            extension=self._extension(filename),
            source_created_at=self._iso(getattr(source, "source_created_at", None)),
            source_modified_at=self._iso(getattr(source, "source_modified_at", None)),
            width=self._positive_int(
                metadata, "width", "image_width", "video_width"
            ),
            height=self._positive_int(
                metadata, "height", "image_height", "video_height"
            ),
            duration_ms=self._positive_int(
                metadata, "duration_ms", "durationMillis"
            ),
            file_size_bytes=self._positive_value(
                getattr(source, "size_bytes", None)
            ),
        )
        self._details_by_source[cache_key] = details
        return details

    def clear_page_cache(self) -> None:
        self._sources_by_asset.clear()
        self._details_by_source.clear()

    def clear(self) -> None:
        self._parent_maps.clear()
        self.clear_page_cache()

    def _ancestor_ids(self, source: SourceAssetModel) -> tuple[str, ...]:
        own_id = str(source.external_asset_id or "").strip()
        if not own_id:
            return ()
        parents = self._parent_map(
            source.tenant_id, source.external_source_id
        )
        result: list[str] = [own_id]
        seen = {own_id}
        frontier = list(parents.get(own_id, ()))
        while frontier and len(result) < 128:
            item = str(frontier.pop(0)).strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
            frontier.extend(parents.get(item, ()))
        return tuple(result)

    def _parent_map(
        self, tenant_id: str, external_source_id: str
    ) -> dict[str, tuple[str, ...]]:
        key = (tenant_id, external_source_id)
        cached = self._parent_maps.get(key)
        if cached is not None:
            return cached
        rows = self.session.execute(
            select(
                SourceAssetModel.external_asset_id,
                SourceAssetModel.source_metadata,
            ).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == external_source_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        ).all()
        mapping = {
            str(external_id): self._parent_ids(
                metadata if isinstance(metadata, Mapping) else {}
            )
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

    @staticmethod
    def _source_provider(source_type: str) -> str:
        return {
            "google_drive": "google-drive",
            "sharepoint": "sharepoint",
        }.get(str(source_type or "").casefold(), "")

    @staticmethod
    def _media_kind(mime_type: str) -> str:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type == "application/pdf":
            return "pdf"
        return "document" if mime_type else ""

    @staticmethod
    def _extension(filename: str) -> str:
        name = filename.rsplit("/", 1)[-1]
        if "." not in name or name.endswith("."):
            return ""
        return name.rsplit(".", 1)[-1].casefold()[:32]

    @staticmethod
    def _iso(value: Any) -> str | None:
        try:
            return value.isoformat()
        except (AttributeError, ValueError):
            return None

    @classmethod
    def _positive_int(
        cls, metadata: Mapping[str, Any], *keys: str
    ) -> int | None:
        for key in keys:
            value = cls._positive_value(metadata.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _positive_value(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return integer if integer >= 0 else None
