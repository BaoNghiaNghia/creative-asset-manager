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
    Provider,
    SearchRequest,
    SearchResponse,
)
from app.modules.explorer.service import ExplorerService
from app.providers.source_factory import create_source_provider
from app.providers.google.auth import get_access_token as get_google_token
from app.providers.google.auth import get_session as get_google_session
from app.providers.google.drive import close_media_stream as close_google_media
from app.providers.google.drive import open_media_stream as open_google_media
from app.providers.microsoft.auth import get_access_token as get_microsoft_token
from app.providers.microsoft.auth import get_session as get_microsoft_session
from app.providers.microsoft.sharepoint import close_media_stream as close_sharepoint_media
from app.providers.microsoft.sharepoint import open_media_stream as open_sharepoint_media

router = APIRouter(prefix="/explorer", tags=["explorer"])


def _account_id(request: Request, provider: Provider) -> str:
    session = (
        get_microsoft_session(request)
        if provider == "sharepoint"
        else get_google_session(request)
    )
    if not session:
        return f"{provider}:developer"
    return str(session.user.get("id") or session.user.get("email") or f"{provider}-user")


async def _access_token(request: Request, provider: Provider) -> str | None:
    token = (
        await get_microsoft_token(request)
        if provider == "sharepoint"
        else await get_google_token(request)
    )
    if provider == "sharepoint" and not token:
        raise HTTPException(status_code=401, detail="Connect SharePoint before browsing files.")
    return token


def _provider_error(exc: Exception, detail: str) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403, 404, 416, 429}:
            return HTTPException(status_code=status, detail=detail)
    if isinstance(exc, (PermissionError, ValueError)):
        return HTTPException(status_code=401, detail=str(exc))
    return HTTPException(status_code=502, detail=detail)


@router.get("/children", response_model=FolderListing)
async def children(
    request: Request,
    parent_id: str = Query("root"),
    provider: Provider = Query("google-drive"),
):
    try:
        token = await _access_token(request, provider)
        return await ExplorerService(create_source_provider).list_folder(
            parent_id,
            token,
            _account_id(request, provider),
            provider,
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to load {provider} folder") from exc


@router.get("/folders", response_model=list[AssetNode])
async def folders(
    request: Request,
    parent_id: str = Query("root"),
    provider: Provider = Query("google-drive"),
):
    try:
        token = await _access_token(request, provider)
        return await ExplorerService(create_source_provider).list_folders(parent_id, token, provider)
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to expand {provider} folder") from exc


@router.post("/index/start", response_model=IndexStatus)
async def start_index(request: Request, body: IndexRequest):
    token = await _access_token(request, body.provider)
    return start_index_job(
        _account_id(request, body.provider),
        token,
        body,
    )


@router.get("/index/status", response_model=IndexStatus)
async def index_status(
    request: Request,
    provider: Provider = Query("google-drive"),
):
    return get_index_status(_account_id(request, provider), provider)


@router.post("/search", response_model=SearchResponse)
async def search(request: Request, body: SearchRequest):
    try:
        token = await _access_token(request, body.provider)
        return await ExplorerService(create_source_provider).search_subtree(
            body,
            token,
            _account_id(request, body.provider),
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to search {body.provider} metadata") from exc


@router.post("/search/stream")
async def search_stream(request: Request, body: SearchRequest):
    token = await _access_token(request, body.provider)
    account_id = _account_id(request, body.provider)

    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def progress(event: dict):
            await queue.put({"type": "progress", **event})

        async def execute():
            try:
                result = await ExplorerService(create_source_provider).search_subtree(
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
                error = _provider_error(exc, f"Unable to search {body.provider} metadata")
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
async def media(
    request: Request,
    item_id: str,
    provider: Provider = Query("google-drive"),
):
    try:
        token = await _access_token(request, provider)
        if not token:
            raise HTTPException(status_code=401, detail=f"Connect {provider} to preview files.")

        opener = open_sharepoint_media if provider == "sharepoint" else open_google_media
        closer = close_sharepoint_media if provider == "sharepoint" else close_google_media
        client, upstream = await opener(token, item_id, request.headers.get("range"))
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
            background=BackgroundTask(closer, client, upstream),
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to stream {provider} file") from exc
