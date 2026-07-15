import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.modules.explorer.schema import AssetNode, FolderListing
from app.modules.explorer.service import ExplorerService
from app.providers.google.auth import get_access_token
from app.providers.google.drive import close_media_stream, open_media_stream

router = APIRouter(prefix="/explorer", tags=["explorer"])


def _drive_error(exc: Exception, detail: str = "Unable to load Google Drive folder") -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403, 404, 416}:
            return HTTPException(status_code=status, detail=detail)
    return HTTPException(status_code=502, detail=detail)


@router.get("/children", response_model=FolderListing)
async def children(request: Request, parent_id: str = Query("root")):
    try:
        token = await get_access_token(request)
        return await ExplorerService().list_folder(parent_id, token)
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration) as exc:
        raise _drive_error(exc) from exc


@router.get("/folders", response_model=list[AssetNode])
async def folders(request: Request, parent_id: str = Query("root")):
    """Fast tree expansion endpoint: fetch folder children only."""
    try:
        token = await get_access_token(request)
        return await ExplorerService().list_folders(parent_id, token)
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration) as exc:
        raise _drive_error(exc) from exc


@router.get("/media/{item_id}")
async def media(request: Request, item_id: str):
    """Stream private Drive media and preserve Range responses for video seeking."""
    try:
        token = await get_access_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Connect Google Drive to preview files.")

        client, upstream = await open_media_stream(
            token,
            item_id,
            request.headers.get("range"),
        )
        passthrough_headers = {
            name: value
            for name in ("content-length", "content-range", "accept-ranges", "etag", "last-modified")
            if (value := upstream.headers.get(name))
        }
        passthrough_headers["cache-control"] = "private, max-age=300"

        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/octet-stream"),
            headers=passthrough_headers,
            background=BackgroundTask(close_media_stream, client, upstream),
        )
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise _drive_error(exc, "Unable to stream Google Drive file") from exc
