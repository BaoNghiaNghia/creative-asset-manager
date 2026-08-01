import asyncio
import contextlib
import json
import logging
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
from app.modules.authorization.folder_scope import (
    ViewerFolderAccess,
    ViewerFolderScopeService,
    viewer_folder_hierarchy_cache,
)
from app.modules.authorization.folder_scope_cache import viewer_folder_remote_parent_cache
from app.providers.source_factory import create_source_provider
from app.providers.google.auth import get_access_token as get_google_token
from app.providers.google.auth import get_session as get_google_session
from app.providers.google.drive import close_media_stream as close_google_media
from app.providers.google.drive import close_thumbnail_stream as close_google_thumbnail
from app.providers.google.drive import GoogleDriveThumbnailUnavailable
from app.providers.google.drive import open_media_stream as open_google_media
from app.providers.google.drive import open_thumbnail_stream as open_google_thumbnail
from app.providers.microsoft.auth import get_access_token as get_microsoft_token
from app.providers.microsoft.auth import get_session as get_microsoft_session
from app.providers.microsoft.sharepoint import close_media_stream as close_sharepoint_media
from app.providers.microsoft.sharepoint import open_media_stream as open_sharepoint_media

router = APIRouter(prefix="/explorer", tags=["explorer"])
logger = logging.getLogger(__name__)


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


def _require_viewer_folder_scope(
    scope_service: ViewerFolderScopeService,
    *,
    tenant_id: str,
    access: ViewerFolderAccess,
    folder_id: str,
    allow_root: bool = True,
) -> None:
    """Require an assigned folder or one of its descendants for Viewer access."""
    if (
        access.restricted
        and (folder_id != "root" or not allow_root)
        and (
            folder_id == "root"
            or not scope_service.allows_external_asset(
                tenant_id=tenant_id,
                access=access,
                external_asset_id=folder_id,
            )
        )
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "viewer_folder_scope_denied",
                "message": "Folder is outside the viewer folder scope.",
            },
        )


_VIEWER_MEDIA_MAX_ANCESTOR_DEPTH = 64


async def _viewer_folder_scope_allowed(
    scope_service: ViewerFolderScopeService,
    *,
    tenant_id: str,
    access: ViewerFolderAccess,
    provider: Provider,
    token: str,
    item_id: str,
) -> bool:
    """Allow a scoped Drive item through local or authoritative parent ancestry."""
    if not access.restricted or scope_service.allows_external_asset(
        tenant_id=tenant_id, access=access, external_asset_id=item_id,
    ):
        return True
    if provider != "google-drive" or not access.source_id:
        return False

    # New or incompletely synced files/folders may not have local ancestry.
    # Cache only immediate parent IDs; caller-specific scope evaluation remains
    # uncached. A lazy client keeps cached/follower requests from creating a
    # provider client, while the first deep lookup still reuses one client.
    async with contextlib.AsyncExitStack() as stack:
        source_client = None

        async def load_parent(current_id: str) -> str | None:
            nonlocal source_client
            if source_client is None:
                source_client = await stack.enter_async_context(
                    create_source_provider(provider, token)
                )
            return (await source_client.get_node(current_id)).parent_id

        current_id = item_id
        visited: set[str] = set()
        for _depth in range(_VIEWER_MEDIA_MAX_ANCESTOR_DEPTH):
            if current_id in visited:
                return False
            visited.add(current_id)
            parent_id = await viewer_folder_remote_parent_cache.get_or_load(
                tenant_id=tenant_id,
                external_source_id=access.source_id,
                item_id=current_id,
                loader=lambda current_id=current_id: load_parent(current_id),
            )
            if not parent_id:
                return False
            if parent_id in access.folder_ids or scope_service.allows_external_asset(
                tenant_id=tenant_id, access=access, external_asset_id=parent_id,
            ):
                return True
            current_id = parent_id
    return False


async def _viewer_media_scope_allowed(
    scope_service: ViewerFolderScopeService,
    *,
    tenant_id: str,
    access: ViewerFolderAccess,
    provider: Provider,
    token: str,
    item_id: str,
) -> bool:
    """Compatibility wrapper for source-media proxy authorization."""
    return await _viewer_folder_scope_allowed(
        scope_service,
        tenant_id=tenant_id,
        access=access,
        provider=provider,
        token=token,
        item_id=item_id,
    )


async def _require_viewer_folder_scope_from_provider(
    scope_service: ViewerFolderScopeService,
    *,
    tenant_id: str,
    access: ViewerFolderAccess,
    provider: Provider,
    token: str,
    folder_id: str,
    allow_root: bool = True,
) -> None:
    if not access.restricted or (folder_id == "root" and allow_root):
        return
    if await _viewer_folder_scope_allowed(
        scope_service,
        tenant_id=tenant_id,
        access=access,
        provider=provider,
        token=token,
        item_id=folder_id,
    ):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "viewer_folder_scope_denied",
            "message": "Folder is outside the viewer folder scope.",
        },
    )


async def _source_context(
    request: Request,
    provider: Provider,
    session: Session,
    principal: CurrentPrincipal,
    external_source_id: str | None = None,
    require_drive_write_scope: bool = False,
) -> tuple[str | None, str, str, str | None]:
    """Return the configured tenant source for Google, never the viewer token."""
    if provider == "google-drive":
        source = await TenantSourceResolver(session).google_drive(
            tenant_id=principal.active_tenant_id,
            external_source_id=external_source_id,
            require_drive_write_scope=require_drive_write_scope,
        )
        return source.access_token, source.provider_account_id, principal.active_tenant_id, source.external_source_id
    token = await _access_token(request, provider)
    return token, _account_id(request, provider), principal.active_tenant_id, external_source_id


async def _authorized_file_context(
    request: Request,
    item_id: str,
    provider: Provider,
    session: Session,
    principal: CurrentPrincipal,
    external_source_id: str | None,
) -> tuple[str, str, str | None]:
    """Resolve a tenant source and enforce the same file scope for all proxies."""
    token, _account_id_value, tenant_id, resolved_source_id = await _source_context(
        request, provider, session, principal, external_source_id
    )
    if not token:
        raise HTTPException(status_code=401, detail=f"Connect {provider} to preview files.")

    scope_service = ViewerFolderScopeService(session)
    access = scope_service.access(
        tenant_id=tenant_id,
        membership_id=principal.membership_id,
        roles=principal.effective_roles,
        external_source_id=resolved_source_id,
    )
    if not await _viewer_media_scope_allowed(
        scope_service,
        tenant_id=tenant_id,
        access=access,
        provider=provider,
        token=token,
        item_id=item_id,
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "viewer_folder_scope_denied",
                "message": "File is outside the viewer folder scope.",
            },
        )
    return token, tenant_id, resolved_source_id


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
    page_token: str | None = Query(None),
    page_size: int = Query(100, ge=1, le=200),
):
    try:
        token, account_id, tenant_id, resolved_source_id = await _source_context(
            request, provider, session, principal, external_source_id
        )
        scope_service = ViewerFolderScopeService(session)
        access = scope_service.access(
            tenant_id=tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=resolved_source_id,
        )
        await _require_viewer_folder_scope_from_provider(
            scope_service,
            tenant_id=tenant_id,
            access=access,
            provider=provider,
            token=token,
            folder_id=parent_id,
        )
        return await ExplorerService(
            create_source_provider, AssetProcessingStatusService(session), access,
        ).list_folder(
            parent_id, token, account_id, provider, tenant_id, resolved_source_id,
            viewer_parent_authorized=parent_id != "root",
            page_token=page_token,
            page_size=page_size,
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
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    try:
        token, _account_id_value, tenant_id, resolved_source_id = await _source_context(
            request, provider, session, principal, external_source_id
        )
        scope_service = ViewerFolderScopeService(session)
        access = scope_service.access(
            tenant_id=tenant_id, membership_id=principal.membership_id,
            roles=principal.effective_roles, external_source_id=resolved_source_id,
        )
        await _require_viewer_folder_scope_from_provider(
            scope_service,
            tenant_id=tenant_id,
            access=access,
            provider=provider,
            token=token,
            folder_id=parent_id,
        )
        return await ExplorerService(create_source_provider, viewer_access=access).list_folders(
            parent_id, token, provider, viewer_parent_authorized=parent_id != "root",
        )
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
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive":
        raise HTTPException(status_code=501, detail="Upload is not supported for this provider yet.")
    if not filename.strip():
        raise HTTPException(status_code=422, detail="A file name is required.")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=422, detail="The selected file is empty.")
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Files larger than 100 MB cannot be uploaded.")
    try:
        token, _account, tenant_id, resolved_source_id = await _source_context(
            request,
            provider,
            session,
            principal,
            external_source_id,
            require_drive_write_scope=True,
        )
        scope_service = ViewerFolderScopeService(session)
        access = scope_service.access(
            tenant_id=tenant_id,
            membership_id=principal.membership_id,
            roles=principal.effective_roles,
            external_source_id=resolved_source_id,
        )
        _require_viewer_folder_scope(
            scope_service,
            tenant_id=tenant_id,
            access=access,
            folder_id=parent_id,
            allow_root=False,
        )
        if not token:
            raise HTTPException(status_code=401, detail="Connect Google Drive before uploading.")
        async with create_source_provider(provider, token) as client:
            parent = await client.get_node(parent_id)
            if parent.kind != "folder":
                raise HTTPException(status_code=422, detail="Destination must be a folder.")
            node = await client.upload_file(parent_id, filename, mime_type, content)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Google Drive denied creating a file in this folder. Reconnect the Drive source "
                    "with read/write access, then confirm that the connected Google account can edit this folder."
                ),
            ) from exc
        raise _provider_error(exc, "Google Drive could not upload this file.") from exc
    except httpx.HTTPError as exc:
        raise _provider_error(exc, "Google Drive could not upload this file.") from exc
    except Exception as exc:
        logger.exception("explorer_upload_failed", extra={"provider": provider})
        raise HTTPException(
            status_code=502,
            detail="Google Drive could not upload this file. Please try again or reconnect the Drive source.",
        ) from exc
    viewer_folder_hierarchy_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    viewer_folder_remote_parent_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    return {"id": node.id, "name": node.name, "kind": node.kind}

@router.delete("/items/{item_id}")
async def delete_item(
    request: Request, item_id: str, provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive": raise HTTPException(status_code=501, detail="Delete is not supported for this provider yet.")
    token, _account, tenant_id, resolved_source_id = await _source_context(request, provider, session, principal, external_source_id)
    if not token: raise HTTPException(status_code=401, detail="Connect Google Drive before deleting files.")
    async with create_source_provider(provider, token) as client:
        await client.get_node(item_id)
        await client.delete_file(item_id)
    viewer_folder_hierarchy_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    viewer_folder_remote_parent_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    return {"deleted": True, "id": item_id}

@router.post("/items/{item_id}/copy")
async def copy_item(
    request: Request, item_id: str, destination_parent_id: str = Query(...), provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive":
        raise HTTPException(status_code=501, detail="Copy is not supported for this provider yet.")
    token, _account, tenant_id, resolved_source_id = await _source_context(
        request, provider, session, principal, external_source_id, require_drive_write_scope=True,
    )
    if not token:
        raise HTTPException(status_code=401, detail="Connect Google Drive before copying files.")
    async with create_source_provider(provider, token) as client:
        destination = await client.get_node(destination_parent_id)
        if destination.kind != "folder":
            raise HTTPException(status_code=422, detail="Destination must be a folder.")
        node = await client.copy_file(item_id, destination_parent_id)
    viewer_folder_hierarchy_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    viewer_folder_remote_parent_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    return {"id": node.id, "parent_id": node.parent_id, "name": node.name}

@router.post("/items/{item_id}/move")
async def move_item(
    request: Request, item_id: str, destination_parent_id: str = Query(...), provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive": raise HTTPException(status_code=501, detail="Move is not supported for this provider yet.")
    token, _account, tenant_id, resolved_source_id = await _source_context(request, provider, session, principal, external_source_id)
    if not token: raise HTTPException(status_code=401, detail="Connect Google Drive before moving files.")
    async with create_source_provider(provider, token) as client:
        destination = await client.get_node(destination_parent_id)
        if destination.kind != "folder": raise HTTPException(status_code=422, detail="Destination must be a folder.")
        node = await client.move_file(item_id, destination_parent_id)
    viewer_folder_hierarchy_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
    viewer_folder_remote_parent_cache.invalidate(
        tenant_id=tenant_id, external_source_id=resolved_source_id,
    )
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
                external_source_id=resolved_source_id,
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
                    external_source_id=resolved_source_id,
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


@router.get("/thumbnail/{item_id}")
async def thumbnail(
    request: Request,
    item_id: str,
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive":
        raise HTTPException(status_code=404, detail="Thumbnail proxy is unavailable for this provider.")
    try:
        token, tenant_id_value, resolved_source_id = await _authorized_file_context(
            request, item_id, provider, session, principal, external_source_id
        )
        shared_client = getattr(request.app.state, "google_drive_stream_client", None)
        client, upstream = await open_google_thumbnail(
            token,
            item_id,
            cache_key=(str(tenant_id_value), str(resolved_source_id), item_id),
            http_client=shared_client,
        )
        passthrough_headers = {
            name: value
            for name in ("content-length", "etag", "last-modified")
            if (value := upstream.headers.get(name))
        }
        passthrough_headers.update(
            {
                "cache-control": "private, max-age=3600, stale-while-revalidate=300",
                "vary": "Cookie",
            }
        )
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type") or "image/jpeg",
            headers=passthrough_headers,
            background=BackgroundTask(
                close_google_thumbnail,
                client,
                upstream,
                client is not shared_client,
            ),
        )
    except GoogleDriveThumbnailUnavailable as exc:
        raise HTTPException(status_code=404, detail="Thumbnail is unavailable.") from exc
    except HTTPException:
        raise
    except (httpx.HTTPError, PermissionError, ValueError) as exc:
        raise _provider_error(exc, "Unable to stream google-drive thumbnail") from exc


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
        token, tenant_id, resolved_source_id = await _authorized_file_context(
            request, item_id, provider, session, principal, external_source_id
        )
        if provider == "sharepoint":
            client, upstream = await open_sharepoint_media(
                token, item_id, request.headers.get("range")
            )
            close_stream = BackgroundTask(close_sharepoint_media, client, upstream)
        else:
            shared_client = getattr(request.app.state, "google_drive_stream_client", None)
            client, upstream = await open_google_media(
                token,
                item_id,
                request.headers.get("range"),
                http_client=shared_client,
            )
            close_stream = BackgroundTask(
                close_google_media,
                client,
                upstream,
                client is not shared_client,
            )
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
            background=close_stream,
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, PermissionError, ValueError) as exc:
        raise _provider_error(exc, f"Unable to stream {provider} file") from exc
