import asyncio
import contextlib
import json

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.modules.explorer.indexing import get_index_status, start_index_job
from app.modules.explorer.schema import (
    AssetNode,
    FolderListing,
    IndexRequest,
    IndexStatus,
    SearchRequest,
    SearchResponse,
)
from app.modules.explorer.service import ExplorerService
from app.providers.google.auth import get_access_token, get_session
from app.providers.google.drive import close_media_stream, open_media_stream

router = APIRouter(prefix="/explorer", tags=["explorer"])


def _account_id(request: Request) -> str:
    session = get_session(request)
    if not session:
        return "developer"
    return str(session.user.get("id") or session.user.get("email") or "google-user")


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
        return await ExplorerService().list_folder(parent_id, token, _account_id(request))
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


@router.post("/index/start", response_model=IndexStatus)
async def start_index(request: Request, body: IndexRequest):
    """Start or reuse the account-scoped background Drive metadata indexing job."""
    token = await get_access_token(request)
    return start_index_job(_account_id(request), token, body)


@router.get("/index/status", response_model=IndexStatus)
async def index_status(request: Request):
    """Return live progress for the signed-in account's metadata indexing job."""
    return get_index_status(_account_id(request))


@router.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest):
    """Search the current Drive folder and all descendants through the metadata index."""
    try:
        token = await get_access_token(request)
        return await ExplorerService().search_subtree(body, token, _account_id(request))
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration) as exc:
        raise _drive_error(exc, "Unable to search Google Drive metadata") from exc


@router.post("/search/stream")
async def search_stream(request: Request, body: SearchRequest):
    """Stream newline-delimited search progress followed by the final result."""
    token = await get_access_token(request)
    account_id = _account_id(request)

    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def progress(event: dict):
            await queue.put({"type": "progress", **event})

        async def execute():
            try:
                result = await ExplorerService().search_subtree(
                    body,
                    token,
                    account_id,
                    progress=progress,
                )
                await queue.put({
                    "type": "result",
                    "status": "Search complete",
                    "progress": 100,
                    "data": jsonable_encoder(result),
                })
            except Exception as exc:
                error = _drive_error(exc, "Unable to search Google Drive metadata")
                await queue.put({
                    "type": "error",
                    "status": "Search failed",
                    "progress": 100,
                    "detail": error.detail,
                })

        task = asyncio.create_task(execute())
        try:
            while True:
                event = await queue.get()
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in {"result", "error"}:
                    break
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


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
