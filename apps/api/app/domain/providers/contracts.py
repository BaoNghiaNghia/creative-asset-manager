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
    analysis_id: str
    metadata: Mapping[str, Any]
    document_hash: str


@dataclass(frozen=True, slots=True)
class StoredMetadataSidecar:
    storage_key: str
    remote_file_id: str | None = None
    remote_folder_id: str | None = None
    web_url: str | None = None
    document_hash: str | None = None


@dataclass(frozen=True, slots=True)
class OpenStoredAssetInput:
    tenant_id: str
    asset_id: str
    remote_file_id: str
    content_type: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DeleteStoredAssetInput:
    """Identity of a temporary managed-storage object eligible for removal."""

    tenant_id: str
    asset_id: str
    remote_file_id: str


@dataclass(slots=True)
class StoredAssetReadStream:
    body: AsyncIterator[bytes]
    close: Callable[[], Awaitable[None]]
    content_type: str = "application/octet-stream"
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class AiMetadataAnalysisInput:
    tenant_id: str
    asset_id: str
    prompt: str
    image_bytes: bytes
    image_mime_type: str
    metadata_profile: str
    metadata_profile_version: str
    image_width: int | None = None
    image_height: int | None = None
    json_schema: Mapping[str, Any] | None = None
    is_cancelled: Callable[[], bool] | None = None
    # Correlation values are optional and never included in the provider request.
    analysis_id: str | None = None
    pipeline_id: str | None = None
    # Internal scheduling hint; never populated directly from a browser request.
    preferred_model: str | None = None


class StorageProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        code: str = "storage_provider_error",
        status_code: int | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.details = dict(details or {})


class AiProviderError(RuntimeError):
    def __init__(
        self, message: str, *, code: str, retryable: bool,
        status_code: int | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class AiMetadataAnalysisResult:
    metadata: Mapping[str, Any]
    provider: str
    model: str | None = None
    provider_request_id: str | None = None

    usage: Mapping[str, Any] = field(default_factory=dict)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    raw_response: Mapping[str, Any] | None = None

@dataclass(frozen=True, slots=True)
class AiBatchSubmissionInput:
    tenant_id: str
    submission_key: str
    display_name: str
    model: str
    input_path: str
    item_count: int
    total_bytes: int
    credential_fingerprint: str | None = None
    credential_encrypted_secret: str | None = None
    credential_key_version: str | None = None


@dataclass(frozen=True, slots=True)
class AiBatchSubmission:
    provider_batch_id: str
    state: str
    provider_request_id: str | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    credential_fingerprint: str | None = None
    credential_encrypted_secret: str | None = None
    credential_key_version: str | None = None


@dataclass(frozen=True, slots=True)
class AiBatchStatusInput:
    tenant_id: str
    provider_batch_id: str
    credential_fingerprint: str | None = None
    credential_encrypted_secret: str | None = None
    credential_key_version: str | None = None


@dataclass(frozen=True, slots=True)
class AiBatchStatus:
    state: str
    retry_after_seconds: float | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class AiBatchResult:
    custom_item_id: str
    result: AiMetadataAnalysisResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    provider_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class AiBatchResultsInput:
    tenant_id: str
    provider_batch_id: str
    cursor: str | None = None
    credential_fingerprint: str | None = None
    credential_encrypted_secret: str | None = None
    credential_key_version: str | None = None


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

    async def open_asset(self, input: OpenStoredAssetInput) -> StoredAssetReadStream: ...

    async def delete_asset(self, input: DeleteStoredAssetInput) -> None: ...

    async def store_metadata_sidecar(
        self, input: StoreMetadataSidecarInput
    ) -> StoredMetadataSidecar: ...


@runtime_checkable
class AiMetadataProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def supports_single(self) -> bool: ...

    @property
    def supports_batch(self) -> bool: ...

    @property
    def default_model(self) -> str | None: ...

    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult: ...

    async def submit_batch(
        self, input: AiBatchSubmissionInput
    ) -> AiBatchSubmission: ...

    async def get_batch_status(
        self, input: AiBatchStatusInput
    ) -> AiBatchStatus: ...

    def stream_batch_results(
        self, input: AiBatchResultsInput
    ) -> AsyncIterator[AiBatchResult]: ...

    async def cancel_batch(self, input: AiBatchStatusInput) -> bool: ...
