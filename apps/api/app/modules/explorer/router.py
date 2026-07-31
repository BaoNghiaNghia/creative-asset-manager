import asyncio
import contextlib
import json
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import get_db
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Config, ElasticsearchV2Index
from app.modules.assets.status_service import AssetProcessingStatusService
from app.modules.assets.model import SourceAssetModel
from app.modules.search.query_builder import ElasticsearchQueryBuilder, SearchQueryConfig
from app.modules.search.query_parser import SearchQueryParser
from app.modules.search.shadow_runtime import SHADOW_SEARCH
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
from app.modules.explorer.media_types import infer_media_type
from app.modules.explorer.tenant_source import TenantSourceResolver
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.authorization.folder_scope import ViewerFolderScopeService
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


def _tenant_id(request: Request, provider: Provider) -> str:
    session = (
        get_microsoft_session(request)
        if provider == "sharepoint"
        else get_google_session(request)
    )
    if session and session.active_tenant_id:
        return str(session.active_tenant_id)
    return _account_id(request, provider)

async def _access_token(request: Request, provider: Provider) -> str | None:
    token = (
        await get_microsoft_token(request)
        if provider == "sharepoint"
        else await get_google_token(request)
    )
    if provider == "sharepoint" and not token:
        raise HTTPException(status_code=401, detail="Connect SharePoint before browsing files.")
    return token


ASSETS_READ = require_permission("assets.read")


async def _source_context(
    request: Request,
    provider: Provider,
    session: Session,
    principal: CurrentPrincipal,
    external_source_id: str | None = None,
) -> tuple[str | None, str, str, str | None]:
    """Return the configured tenant source for Google, never the viewer token."""
    if provider == "google-drive":
        source = await TenantSourceResolver(session).google_drive(
            tenant_id=principal.active_tenant_id,
            external_source_id=external_source_id,
        )
        return source.access_token, source.provider_account_id, principal.active_tenant_id, source.external_source_id
    token = await _access_token(request, provider)
    return token, _account_id(request, provider), principal.active_tenant_id, external_source_id


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
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    try:
        token, account_id, tenant_id, resolved_source_id = await _source_context(
            request, provider, session, principal, external_source_id
        )
        access = ViewerFolderScopeService(session).access(
            tenant_id=tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=resolved_source_id,
        )
        return await ExplorerService(
            create_source_provider, AssetProcessingStatusService(session), access,
        ).list_folder(parent_id, token, account_id, provider, tenant_id, resolved_source_id)
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to load {provider} folder") from exc


@router.get("/folders", response_model=list[AssetNode])
async def folders(
    request: Request,
    parent_id: str = Query("root"),
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    try:
        token, _account_id_value, tenant_id, resolved_source_id = await _source_context(
            request, provider, session, principal, external_source_id
        )
        access = ViewerFolderScopeService(session).access(
            tenant_id=tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=resolved_source_id,
        )
        return await ExplorerService(create_source_provider, viewer_access=access).list_folders(parent_id, token, provider)
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to expand {provider} folder") from exc


@router.post("/index/start", response_model=IndexStatus)
async def start_index(
    request: Request,
    body: IndexRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
):
    token, account_id, _tenant_id_value, _resolved_source_id = await _source_context(
        request, body.provider, session, principal
    )
    return start_index_job(account_id, token, body)


@router.get("/index/status", response_model=IndexStatus)
async def index_status(
    request: Request,
    provider: Provider = Query("google-drive"),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
):
    return get_index_status(principal.active_tenant_id, provider)


async def _v2_shadow(body: SearchRequest, tenant: str, settings):
    parsed = SearchQueryParser().parse(body.query)
    query = ElasticsearchQueryBuilder().build(
        parsed, tenant_id=tenant, config=SearchQueryConfig(),
        size=min(body.limit, 200), offset=0,
    )
    async with ElasticsearchV2Index(ElasticsearchV2Config(
        settings.ELASTICSEARCH_URL, settings.ELASTICSEARCH_INDEX_PREFIX,
    )) as index:
        response = await index.search(query)
    return {
        "items": response.get("hits", {}).get("hits", []),
        "total": response.get("hits", {}).get("total", 0),
    }


@router.get("/viewer-folder-options")
async def viewer_folder_options(
    request: Request,
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("tenant_members.manage")),
    external_source_id: str | None = Query(None),
):
    token, _account_id_value, _tenant_id_value, resolved_source_id = await _source_context(
        request, provider, session, principal, external_source_id
    )
    if provider != "google-drive":
        return {"external_source_id": resolved_source_id, "folders": []}
    async with create_source_provider(provider, token) as client:
        folders = await client.list_children("root", folders_only=True)
    return {
        "external_source_id": resolved_source_id,
        "folders": [{"id": item.id, "name": item.name} for item in folders],
    }


@router.post("/upload")
async def upload_file(
    request: Request,
    parent_id: str = Query("root"),
    filename: str = Query("upload"),
    mime_type: str = Query("application/octet-stream"),
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive":
        raise HTTPException(status_code=501, detail="Upload is not supported for this provider yet.")
    token, _account, _tenant, _source = await _source_context(request, provider, session, principal, external_source_id)
    if not token: raise HTTPException(status_code=401, detail="Connect Google Drive before uploading.")
    async with create_source_provider(provider, token) as client:
        parent = await client.get_node(parent_id)
        if parent.kind != "folder": raise HTTPException(status_code=422, detail="Destination must be a folder.")
        content = await request.body()
        if len(content) > 100 * 1024 * 1024: raise HTTPException(status_code=413, detail="File is too large.")
        node = await client.upload_file(parent_id, filename, mime_type, content)
    return {"id": node.id, "name": node.name, "kind": node.kind}

@router.delete("/items/{item_id}")
async def delete_item(
    request: Request, item_id: str, provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive": raise HTTPException(status_code=501, detail="Delete is not supported for this provider yet.")
    token, _account, _tenant, _source = await _source_context(request, provider, session, principal, external_source_id)
    if not token: raise HTTPException(status_code=401, detail="Connect Google Drive before deleting files.")
    async with create_source_provider(provider, token) as client:
        await client.get_node(item_id)
        await client.delete_file(item_id)
    return {"deleted": True, "id": item_id}

@router.post("/items/{item_id}/move")
async def move_item(
    request: Request, item_id: str, destination_parent_id: str = Query(...), provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive": raise HTTPException(status_code=501, detail="Move is not supported for this provider yet.")
    token, _account, _tenant, _source = await _source_context(request, provider, session, principal, external_source_id)
    if not token: raise HTTPException(status_code=401, detail="Connect Google Drive before moving files.")
    async with create_source_provider(provider, token) as client:
        destination = await client.get_node(destination_parent_id)
        if destination.kind != "folder": raise HTTPException(status_code=422, detail="Destination must be a folder.")
        node = await client.move_file(item_id, destination_parent_id)
    return {"id": node.id, "parent_id": node.parent_id}

@router.post("/search", response_model=SearchResponse)
async def search(
    request: Request,
    body: SearchRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    try:
        token, account_id, tenant_id, resolved_source_id = await _source_context(
            request, body.provider, session, principal, external_source_id
        )
        settings = get_settings()
        access = ViewerFolderScopeService(session).access(
            tenant_id=tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=resolved_source_id,
        )

        async def primary():
            result = await ExplorerService(
                create_source_provider, AssetProcessingStatusService(session), access,
            ).search_subtree(
                body, token, account_id, tenant_id=tenant_id,
            )
            return result.model_dump(mode="json")

        return await SHADOW_SEARCH.execute(
            tenant_id=tenant_id, query=body.query, primary=primary,
            shadow=lambda: _v2_shadow(body, tenant_id, settings),
            primary_version="v1", shadow_version="v2",
            surface="explorer_search",
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to search {body.provider} metadata") from exc


@router.post("/search/stream")
async def search_stream(
    request: Request,
    body: SearchRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    token, account_id, tenant_id, resolved_source_id = await _source_context(
        request, body.provider, session, principal, external_source_id
    )
    access = ViewerFolderScopeService(session).access(
        tenant_id=tenant_id, membership_id=principal.membership_id,
        roles=principal.effective_roles, external_source_id=resolved_source_id,
    )

    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()

        async def progress(event: dict):
            await queue.put({"type": "progress", **event})

        async def execute():
            try:
                started = time.perf_counter()
                result = await ExplorerService(
                    create_source_provider, AssetProcessingStatusService(session), access,
                ).search_subtree(
                    body,
                    token,
                    account_id,
                    progress=progress,
                    tenant_id=tenant_id,
                )
                primary_document = jsonable_encoder(result)
                settings = get_settings()
                await SHADOW_SEARCH.observe(
                    tenant_id=tenant_id, query=body.query,
                    primary_result=primary_document,
                    primary_ms=int((time.perf_counter() - started) * 1000),
                    shadow=lambda: _v2_shadow(body, tenant_id, settings),
                    primary_version="v1", shadow_version="v2",
                    surface="explorer_search_stream",
                )
                await queue.put({
                    "type": "result",
                    "status": "Search complete",
                    "progress": 100,
                    "data": primary_document,
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
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    try:
        token, _account_id_value, tenant_id, resolved_source_id = await _source_context(
            request, provider, session, principal, external_source_id
        )
        access = ViewerFolderScopeService(session).access(
            tenant_id=tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=resolved_source_id,
        )
        if access.restricted and not ViewerFolderScopeService(session).allows_external_asset(
            tenant_id=tenant_id, access=access, external_asset_id=item_id,
        ):
            raise HTTPException(status_code=403, detail={"code": "viewer_folder_scope_denied", "message": "File is outside the viewer folder scope."})
        if not token:
            raise HTTPException(status_code=401, detail=f"Connect {provider} to preview files.")

        opener = open_sharepoint_media if provider == "sharepoint" else open_google_media
        closer = close_sharepoint_media if provider == "sharepoint" else close_google_media
        client, upstream = await opener(token, item_id, request.headers.get("range"))
        source_row = session.execute(
            select(SourceAssetModel.filename, SourceAssetModel.mime_type).where(
                SourceAssetModel.tenant_id == tenant_id,
                SourceAssetModel.external_source_id == resolved_source_id,
                SourceAssetModel.external_asset_id == item_id,
                SourceAssetModel.deleted_at.is_(None),
            )
        ).first()
        filename, declared_mime = source_row if source_row else (None, None)
        media_type = infer_media_type(filename, declared_mime, upstream.headers.get("content-type"))
        passthrough_headers = {
            name: value
            for name in ("content-length", "content-range", "accept-ranges", "etag", "last-modified")
            if (value := upstream.headers.get(name))
        }
        passthrough_headers["cache-control"] = "private, max-age=300"

        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=media_type,
            headers=passthrough_headers,
            background=BackgroundTask(closer, client, upstream),
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to stream {provider} file") from exc
