import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from app.providers.source_factory import create_source_provider
from app.providers.microsoft.onedrive import OneDriveClient,OneDriveDownloadError,OneDriveThumbnailUnavailable,close_thumbnail_stream,open_media_stream,open_thumbnail_stream,validate_graph_url
from app.providers.microsoft.onedrive_mapper import ONEDRIVE_ROOT_ID,make_item_id,map_item,parse_item_id,root_node

def test_item_id_round_trip_and_sharepoint_rejected():
    value=make_item_id("drive:alpha","item/with+chars")
    assert parse_item_id(value)==("drive:alpha","item/with+chars")
    with pytest.raises(ValueError): parse_item_id("sp:item:YWJj:ZGVm")

@pytest.mark.parametrize(("mime","kind"),[("image/png","image"),("video/mp4","video"),("application/pdf","pdf"),("application/vnd.openxmlformats-officedocument.wordprocessingml.document","document"),("text/plain","other")])
def test_mapper_file_kinds(mime,kind):
    node=map_item({"id":"item","name":"file","file":{"mimeType":mime},"parentReference":{"driveId":"parent-drive","id":"parent"}}, "drive")
    assert node.kind==kind and parse_item_id(node.id)==("drive","item") and parse_item_id(node.parent_id)==("parent-drive","parent")

def test_mapper_folder_and_root():
    assert root_node().id==ONEDRIVE_ROOT_ID
    assert map_item({"id":"folder","name":"folder","folder":{"childCount":1}},"drive").kind=="folder"

def test_graph_cursor_is_strictly_graph_v1():
    assert validate_graph_url("https://graph.microsoft.com/v1.0/drives/x/root/delta?$skiptoken=a")
    for value in ("http://graph.microsoft.com/v1.0/x","https://evil.example/v1.0/x","https://graph.microsoft.com/beta/x"):
        with pytest.raises(ValueError): validate_graph_url(value)

def test_factory_creates_onedrive_adapter():
    assert create_source_provider("onedrive","token").source_type=="onedrive"

def test_thumbnail_stream_uses_graph_drive_item_and_closes():
    client=MagicMock()
    client.build_request.return_value=object()
    response=MagicMock(status_code=200)
    response.raise_for_status=MagicMock()
    response.aclose=AsyncMock()
    client.send=AsyncMock(return_value=response)
    client.aclose=AsyncMock()
    item_id=make_item_id("drive-id","item-id")
    with patch("app.providers.microsoft.onedrive.httpx.AsyncClient",return_value=client):
        returned_client,returned_response=asyncio.run(open_thumbnail_stream("secret",item_id))
    assert returned_client is client and returned_response is response
    args,kwargs=client.build_request.call_args
    assert args[0]=="GET"
    assert args[1]=="https://graph.microsoft.com/v1.0/drives/drive-id/items/item-id/thumbnails/0/large/content"
    assert kwargs["headers"]=={"Authorization":"Bearer secret"}
    asyncio.run(close_thumbnail_stream(client,response))
    response.aclose.assert_awaited_once()
    client.aclose.assert_awaited_once()

def test_missing_thumbnail_closes_graph_response_and_client():
    client=MagicMock()
    client.build_request.return_value=object()
    response=MagicMock(status_code=404)
    response.aclose=AsyncMock()
    client.send=AsyncMock(return_value=response)
    client.aclose=AsyncMock()
    with patch("app.providers.microsoft.onedrive.httpx.AsyncClient",return_value=client):
        with pytest.raises(OneDriveThumbnailUnavailable):
            asyncio.run(open_thumbnail_stream("secret",make_item_id("drive-id","item-id")))
    response.aclose.assert_awaited_once()
    client.aclose.assert_awaited_once()

def test_media_stream_uses_graph_download_url_when_consumer_content_endpoint_returns_400():
    client=MagicMock()
    client.build_request.side_effect=["content-request", "download-request"]
    rejected=MagicMock(status_code=400)
    rejected.aclose=AsyncMock()
    metadata=MagicMock(status_code=200)
    metadata.json.return_value={"@microsoft.graph.downloadUrl":"https://public.files.1drv.com/content"}
    downloaded=MagicMock(status_code=200)
    downloaded.raise_for_status=MagicMock()
    client.send=AsyncMock(side_effect=[rejected, downloaded])
    client.get=AsyncMock(return_value=metadata)
    client.aclose=AsyncMock()
    with patch("app.providers.microsoft.onedrive.httpx.AsyncClient",return_value=client):
        returned_client,returned_response=asyncio.run(open_media_stream("secret",make_item_id("drive-id","item-id"),None))
    assert returned_client is client and returned_response is downloaded
    assert client.get.await_args.kwargs["params"]=={"$select":"id,@microsoft.graph.downloadUrl"}
    assert client.build_request.call_args_list[1].args==("GET","https://public.files.1drv.com/content")

def test_media_stream_exposes_graph_error_code_when_fallback_metadata_is_rejected():
    client=MagicMock()
    client.build_request.return_value="content-request"
    rejected=MagicMock(status_code=400)
    rejected.aclose=AsyncMock()
    metadata=MagicMock(status_code=404)
    metadata.aread=AsyncMock(return_value=b'{"error":{"code":"itemNotFound","message":"Missing"}}')
    client.send=AsyncMock(return_value=rejected)
    client.get=AsyncMock(return_value=metadata)
    client.aclose=AsyncMock()
    with patch("app.providers.microsoft.onedrive.httpx.AsyncClient",return_value=client):
        with pytest.raises(OneDriveDownloadError, match="itemNotFound") as exc:
            asyncio.run(open_media_stream("secret",make_item_id("drive-id","item-id"),None))
    assert exc.value.status_code==404 and exc.value.graph_code=="itemNotFound"
    client.aclose.assert_awaited_once()

def test_drive_retries_without_owner_projection_after_consumer_forbidden():
    client=MagicMock()
    forbidden=httpx.HTTPStatusError("forbidden", request=httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/drive"), response=MagicMock(status_code=403))
    graph=OneDriveClient("token", client=client)
    graph._get=AsyncMock(side_effect=[forbidden,{"id":"drive-id","name":"Personal"}])
    assert asyncio.run(graph.drive())["id"]=="drive-id"
    assert graph._get.await_args_list[1].args == ("/me/drive",)
