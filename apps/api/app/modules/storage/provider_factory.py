from __future__ import annotations

from app.core.config import Settings
from app.domain.providers.contracts import AssetStorageProvider
from app.modules.storage.managed_oauth import resolve_managed_storage_credential
from app.providers.google.storage import GoogleDriveAssetStorage
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


def build_managed_storage_provider(settings: Settings) -> AssetStorageProvider:
    credentials = resolve_managed_storage_credential(settings)
    if not (
        settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID
        and (
            credentials.refresh_token
            or credentials.access_token
        )
    ):
        return UnconfiguredAssetStorageProvider()
    return GoogleDriveAssetStorage(
        credentials.access_token,
        root_folder_id=settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID,
        refresh_token=credentials.refresh_token,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )
