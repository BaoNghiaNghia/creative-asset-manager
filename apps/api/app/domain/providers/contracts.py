from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

SourceType = Literal["google_drive", "sharepoint", "external_api"]
SourceChangeType = Literal["created", "updated", "deleted", "restored"]


@dataclass(frozen=True, slots=True)
class ExternalAssetCandidate:
    source_type: SourceType
    source_id: str
    external_asset_id: str
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    source_created_at: str | None = None
    source_modified_at: str | None = None
    provider_checksum: str | None = None
    provider_version: str | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ListSourceChangesInput:
    source_id: str
    cursor: str | None = None
    page_size: int = 100
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    reconciliation: bool = False


@dataclass(frozen=True, slots=True)
class SourceChange:
    change_type: SourceChangeType
    external_asset_id: str
    candidate: ExternalAssetCandidate | None = None


@dataclass(frozen=True, slots=True)
class SourceChangePage:
    changes: tuple[SourceChange, ...]
    next_cursor: str | None = None
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class GetSourceAssetInput:
    source_id: str
    external_asset_id: str


@dataclass(frozen=True, slots=True)
class OpenSourceAssetInput:
    source_id: str
    external_asset_id: str
    range_header: str | None = None


@dataclass(slots=True)
class AssetDownloadStream:
    body: AsyncIterator[bytes]
    close: Callable[[], Awaitable[None]]
    status_code: int = 200
    content_type: str = "application/octet-stream"
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoreAssetInput:
    tenant_id: str
    content_hash: str
    body: AsyncIterator[bytes]
    asset_id: str
    content_type: str | None = None
    size_bytes: int | None = None
    filename: str | None = None


@dataclass(frozen=True, slots=True)
class StoredAsset:
    storage_key: str
    content_hash: str
    size_bytes: int | None = None
    storage_provider: str | None = None
    remote_file_id: str | None = None
    remote_folder_id: str | None = None
    web_url: str | None = None


@dataclass(frozen=True, slots=True)
class StoreMetadataSidecarInput:
    tenant_id: str
    asset_id: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StoredMetadataSidecar:
    storage_key: str


@dataclass(frozen=True, slots=True)
class AiMetadataAnalysisInput:
    tenant_id: str
    asset_id: str
    content_type: str | None = None
    source_url: str | None = None
    profile: Mapping[str, Any] | None = None


class StorageProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AiMetadataAnalysisResult:
    metadata: Mapping[str, Any]
    provider: str
    model: str | None = None
    provider_request_id: str | None = None


@runtime_checkable
class AssetSourceProvider(Protocol):
    async def list_changes(self, input: ListSourceChangesInput) -> SourceChangePage: ...

    async def get_asset(self, input: GetSourceAssetInput) -> ExternalAssetCandidate: ...

    async def open_download_stream(
        self, input: OpenSourceAssetInput
    ) -> AssetDownloadStream: ...


@runtime_checkable
class AssetStorageProvider(Protocol):
    async def store_asset(self, input: StoreAssetInput) -> StoredAsset: ...

    async def store_metadata_sidecar(
        self, input: StoreMetadataSidecarInput
    ) -> StoredMetadataSidecar: ...


@runtime_checkable
class AiMetadataProvider(Protocol):
    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult: ...
