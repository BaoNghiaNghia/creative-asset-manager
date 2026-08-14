from __future__ import annotations

from app.core.config import Settings
from app.domain.providers.contracts import AssetStorageProvider
from app.providers.google.storage import GoogleDriveAssetStorage
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


def build_managed_storage_provider(settings: Settings) -> AssetStorageProvider:
    if not (
        settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID
        and (
            settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN
            or settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN
        )
    ):
        return UnconfiguredAssetStorageProvider()
    return GoogleDriveAssetStorage(
        settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN,
        root_folder_id=settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID,
        refresh_token=settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )
