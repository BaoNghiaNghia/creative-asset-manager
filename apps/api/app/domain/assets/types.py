from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

SourceType = Literal["google_drive", "sharepoint", "external_api"]


@dataclass(frozen=True, slots=True)
class ExternalSourceRecord:
    id: str
    tenant_id: str
    source_key: str
    source_type: SourceType
    display_name: str | None = None


@dataclass(frozen=True, slots=True)
class SourceAssetRecord:
    id: str
    tenant_id: str
    external_source_id: str
    external_asset_id: str
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    source_created_at: datetime | None = None
    source_modified_at: datetime | None = None
    provider_checksum: str | None = None
    provider_version: str | None = None
    hashed_provider_checksum: str | None = None
    hashed_provider_version: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: str
    tenant_id: str
    content_hash: str
    analysis_image_hash: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
