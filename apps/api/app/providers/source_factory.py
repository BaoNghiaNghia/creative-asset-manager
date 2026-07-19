from app.modules.explorer.provider_contract import ExplorerSourceProvider
from app.providers.google.source_adapter import GoogleDriveSourceAdapter
from app.providers.microsoft.source_adapter import SharePointSourceAdapter


def create_source_provider(provider: str, access_token: str) -> ExplorerSourceProvider:
    if provider == "google-drive":
        return GoogleDriveSourceAdapter(access_token)
    if provider == "sharepoint":
        return SharePointSourceAdapter(access_token)
    raise ValueError(f"Unsupported source provider: {provider}")
