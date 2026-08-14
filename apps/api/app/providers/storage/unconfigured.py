from app.domain.providers.contracts import (
    DeleteStoredAssetInput,
    OpenStoredAssetInput,
    StorageProviderError,
    StoredAssetReadStream,
    StoreAssetInput,
    StoredAsset,
    StoredMetadataSidecar,
    StoreMetadataSidecarInput,
)


class UnconfiguredAssetStorageProvider:
    async def delete_asset(self, input: DeleteStoredAssetInput) -> None:
        raise StorageProviderError("managed asset storage is not configured", retryable=False)

    async def open_asset(
        self, input: OpenStoredAssetInput
    ) -> StoredAssetReadStream:
        raise StorageProviderError(
            "managed asset storage is not configured",
            retryable=False,
        )
    async def store_asset(self, input: StoreAssetInput) -> StoredAsset:
        raise RuntimeError("managed asset storage is not configured")

    async def store_metadata_sidecar(
        self, input: StoreMetadataSidecarInput
    ) -> StoredMetadataSidecar:
        raise RuntimeError("metadata sidecar storage is not configured")
