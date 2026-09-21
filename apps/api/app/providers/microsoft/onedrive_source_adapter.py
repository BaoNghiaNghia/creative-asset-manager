from app.domain.providers.contracts import (
    AssetDownloadStream,
    ListSourceChangesInput,
    OpenSourceAssetInput,
    SourceChangePage,
)
from app.providers.microsoft.onedrive import (
    OneDriveClient,
    close_media_stream,
    open_media_stream,
)
from app.providers.microsoft.onedrive_delta import list_onedrive_delta
from app.providers.source_adapter import BaseSourceAdapter


class OneDriveSourceAdapter(BaseSourceAdapter):
    source_type = "onedrive"

    def __init__(
        self,
        access_token,
        client_factory=OneDriveClient,
        media_opener=open_media_stream,
        changes_lister=list_onedrive_delta,
        media_closer=close_media_stream,
        media_http_client=None,
    ):
        super().__init__(access_token, client_factory)
        self._media_opener = media_opener
        self._changes_lister = changes_lister
        self._media_closer = media_closer
        self._media_http_client = media_http_client

    async def list_changes(
        self, input: ListSourceChangesInput
    ) -> SourceChangePage:
        return await self._changes_lister(self._access_token, input)

    async def open_download_stream(
        self, input: OpenSourceAssetInput
    ) -> AssetDownloadStream:
        if self._media_http_client is None:
            client, response = await self._media_opener(
                self._access_token,
                input.external_asset_id,
                input.range_header,
            )
        else:
            client, response = await self._media_opener(
                self._access_token,
                input.external_asset_id,
                input.range_header,
                http_client=self._media_http_client,
            )

        async def close():
            if self._media_http_client is None:
                await self._media_closer(client, response)
            else:
                await self._media_closer(client, response, False)

        return AssetDownloadStream(
            body=response.aiter_raw(),
            close=close,
            status_code=response.status_code,
            content_type=response.headers.get(
                "content-type", "application/octet-stream"
            ),
            headers=dict(response.headers),
        )
