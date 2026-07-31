from app.domain.providers.contracts import (
    AssetDownloadStream,
    ListSourceChangesInput,
    OpenSourceAssetInput,
    SourceChangePage,
)
from app.providers.google.drive import (
    GoogleDriveClient,
    close_media_stream,
    open_media_stream,
)
from app.providers.google.incremental import list_drive_changes
from app.providers.source_adapter import BaseSourceAdapter


class GoogleDriveSourceAdapter(BaseSourceAdapter):
    source_type = "google_drive"

    def __init__(
        self,
        access_token: str,
        client_factory=GoogleDriveClient,
        media_opener=open_media_stream,
        changes_lister=list_drive_changes,
        media_closer=close_media_stream,
    ):
        super().__init__(access_token, client_factory)
        self._media_opener = media_opener
        self._media_closer = media_closer
        self._changes_lister = changes_lister


    async def list_changes(
        self, input: ListSourceChangesInput
    ) -> SourceChangePage:
        return await self._changes_lister(self._access_token, input)
    async def upload_file(self, parent_id: str, filename: str, mime_type: str, content: bytes):
        return await self.client.upload_file(parent_id, filename, mime_type, content)

    async def delete_file(self, item_id: str):
        await self.client.delete_file(item_id)

    async def move_file(self, item_id: str, destination_parent_id: str):
        return await self.client.move_file(item_id, destination_parent_id)

    async def open_download_stream(
        self, input: OpenSourceAssetInput
    ) -> AssetDownloadStream:
        client, response = await self._media_opener(
            self._access_token,
            input.external_asset_id,
            input.range_header,
        )

        async def close() -> None:
            await self._media_closer(client, response)

        return AssetDownloadStream(
            body=response.aiter_raw(),
            close=close,
            status_code=response.status_code,
            content_type=response.headers.get(
                "content-type", "application/octet-stream"
            ),
            headers=dict(response.headers),
        )
