from __future__ import annotations

from app.domain.providers.contracts import (
    AssetStorageProvider,
    StorageProviderError,
    StoreAssetInput,
    StoredAsset,
)
from app.modules.assets.model import AssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.storage.repository import ManagedStorageRepository


class ManagedAssetStorageService:
    def __init__(
        self,
        assets: AssetRegistryRepository,
        storage: ManagedStorageRepository,
        *,
        enabled: bool = False,
        max_attempts: int = 5,
    ):
        if assets.session is not storage.session:
            raise ValueError("asset and storage repositories must share one session")
        self.assets = assets
        self.storage = storage
        self.enabled = enabled
        self.max_attempts = max_attempts

    async def store(
        self, input: StoreAssetInput, provider: AssetStorageProvider
    ) -> StoredAsset:
        if not self.enabled:
            raise RuntimeError("managed asset storage is disabled")
        asset = self.assets.session.get(AssetModel, input.asset_id)
        if asset is None or asset.tenant_id != input.tenant_id:
            raise LookupError(input.asset_id)
        if asset.content_hash != input.content_hash:
            raise ValueError("input content hash does not match internal asset")
        provider_name = getattr(provider, "provider_name", provider.__class__.__name__)
        record = self.storage.get_or_create(
            tenant_id=input.tenant_id,
            asset_id=input.asset_id,
            content_hash=input.content_hash,
            storage_provider=provider_name,
        )
        if record.status == "stored":
            return StoredAsset(
                storage_key=f"google-drive:{record.remote_file_id}",
                content_hash=record.content_hash,
                storage_provider=record.storage_provider,
                remote_file_id=record.remote_file_id,
                remote_folder_id=record.remote_folder_id,
                web_url=record.web_url,
            )
        self.storage.mark_uploading(record)
        self.storage.session.commit()
        try:
            result = await provider.store_asset(input)
            if not result.remote_file_id or not result.remote_folder_id:
                raise StorageProviderError("storage provider returned incomplete identity", retryable=True)
            self.storage.mark_stored(
                record,
                remote_file_id=result.remote_file_id,
                remote_folder_id=result.remote_folder_id,
                web_url=result.web_url,
            )
            self.storage.session.commit()
            return result
        except StorageProviderError as exc:
            self.storage.mark_failure(
                record,
                retryable=exc.retryable,
                error_code=type(exc).__name__,
                error_message=str(exc),
                max_attempts=self.max_attempts,
            )
            self.storage.session.commit()
            raise
        except Exception as exc:
            self.storage.mark_failure(
                record,
                retryable=True,
                error_code=type(exc).__name__,
                error_message=str(exc),
                max_attempts=self.max_attempts,
            )
            self.storage.session.commit()
            raise
