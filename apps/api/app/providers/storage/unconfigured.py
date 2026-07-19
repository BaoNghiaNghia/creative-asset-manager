from app.domain.providers.contracts import (
    StoreAssetInput,
    StoredAsset,
    StoredMetadataSidecar,
    StoreMetadataSidecarInput,
)


class UnconfiguredAssetStorageProvider:
    async def store_asset(self, input: StoreAssetInput) -> StoredAsset:
        raise RuntimeError("managed asset storage is not configured")

    async def store_metadata_sidecar(
        self, input: StoreMetadataSidecarInput
    ) -> StoredMetadataSidecar:
        raise RuntimeError("metadata sidecar storage is not configured")
