from app.modules.explorer.provider_contract import ExplorerSourceProvider
from app.providers.google.source_adapter import GoogleDriveSourceAdapter
from app.providers.microsoft.source_adapter import SharePointSourceAdapter
from app.providers.microsoft.onedrive_source_adapter import OneDriveSourceAdapter


def create_source_provider(provider: str, access_token: str) -> ExplorerSourceProvider:
    if provider == "google-drive":
        return GoogleDriveSourceAdapter(access_token)
    if provider == "onedrive":
        return OneDriveSourceAdapter(access_token)
    if provider == "sharepoint":
        return SharePointSourceAdapter(access_token)
    raise ValueError(f"Unsupported source provider: {provider}")
