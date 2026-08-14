import asyncio
import contextlib
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.core.database import get_db
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index
from app.modules.assets.status_service import AssetProcessingStatusService
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.explorer.indexing import get_index_status, start_index_job
from app.modules.explorer.folder_notes import FolderNoteModel, resolve_note_owner_from_nodes
from app.modules.explorer.schema import (
    AssetNode,
    AssetLocationResponse,
    FolderListing,
    FolderNoteResponse,
    FolderNoteUpdateRequest,
    IndexRequest,
    IndexStatus,
    Provider,
    ViewerBootstrapResponse,
)
from app.modules.explorer.service import ExplorerService
from app.modules.explorer.breadcrumb import location_breadcrumb_cache, resolve_breadcrumb
from app.modules.explorer.media_types import infer_media_type
from app.modules.explorer.preview import PreviewConversionError, convert_avif_to_webp, preview_cache_get, preview_cache_put
from app.modules.explorer.tenant_source import TenantSourceResolver
from app.modules.authorization.principal import CurrentPrincipal, require_permission, is_pure_viewer
from app.modules.authorization.folder_scope import (
    ViewerFolderAccess,
    ViewerFolderScopeModel,
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


def _require_legacy_admin(principal: CurrentPrincipal) -> None:
    if principal.platform_admin or "search.rebuild" in principal.effective_permissions:
        return
    raise HTTPException(status_code=403, detail={
        "code": "permission_required",
        "message": "Legacy Explorer diagnostics require search.rebuild.",
    })


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
        if is_pure_viewer(principal) and not external_source_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "viewer_source_context_required",
                    "message": "Select a Google Drive source before browsing as a viewer.",
                },
            )
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


@router.get("/viewer/bootstrap", response_model=ViewerBootstrapResponse)
def viewer_bootstrap(
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
):
    if not is_pure_viewer(principal):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "viewer_bootstrap_not_applicable",
                "message": "Viewer bootstrap is available only to folder-scoped viewers.",
            },
        )
    source_type = "google_drive" if provider == "google-drive" else "sharepoint"
    rows = session.execute(
        select(ExternalSourceModel, ViewerFolderScopeModel)
        .join(
            ViewerFolderScopeModel,
            and_(
                ViewerFolderScopeModel.tenant_id == ExternalSourceModel.tenant_id,
                ViewerFolderScopeModel.external_source_id == ExternalSourceModel.id,
            ),
        )
        .where(
            ExternalSourceModel.tenant_id == principal.active_tenant_id,
            ExternalSourceModel.source_type == source_type,
            ViewerFolderScopeModel.tenant_id == principal.active_tenant_id,
            ViewerFolderScopeModel.tenant_membership_id == principal.membership_id,
        )
        .order_by(
            ExternalSourceModel.display_name,
            ExternalSourceModel.id,
            ViewerFolderScopeModel.folder_name,
            ViewerFolderScopeModel.folder_external_id,
        )
    ).all()
    if not rows:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "viewer_folder_scope_required",
                "message": "No folders are assigned to this viewer.",
            },
        )
    sources: dict[str, dict] = {}
    for source, scope in rows:
        entry = sources.setdefault(source.id, {
            "external_source_id": source.id,
            "display_name": source.display_name or ("Google Drive" if provider == "google-drive" else "SharePoint"),
            "folders": [],
        })
        entry["folders"].append({
            "id": scope.folder_external_id,
            "name": scope.folder_name or scope.folder_external_id,
            "external_source_id": source.id,
        })
    values = list(sources.values())
    selected_source = values[0] if len(values) == 1 else None
    selected_folder = (
        selected_source["folders"][0]
        if selected_source is not None and len(selected_source["folders"]) == 1
        else None
    )
    return {
        "sources": values,
        "auto_selected_source_id": selected_source["external_source_id"] if selected_source else None,
        "auto_selected_folder_id": selected_folder["id"] if selected_folder else None,
    }


@router.get("/items/{item_id}/location", response_model=AssetLocationResponse)
async def item_location(request: Request, item_id: str, provider: Provider = Query("google-drive"), external_source_id: str | None = Query(None), session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(ASSETS_READ)):
    token, _account_id, tenant_id, resolved_source_id = await _source_context(request, provider, session, principal, external_source_id)
    if not resolved_source_id:
        return AssetLocationResponse(status="unavailable", breadcrumb=[])
    scope_service = ViewerFolderScopeService(session)
    access = scope_service.access(tenant_id=tenant_id, membership_id=principal.membership_id, roles=principal.effective_roles, external_source_id=resolved_source_id)
    if access.restricted and not await _viewer_folder_scope_allowed(scope_service, tenant_id=tenant_id, access=access, provider=provider, token=token or "", item_id=item_id):
        raise HTTPException(status_code=403, detail={"code": "viewer_folder_scope_denied", "message": "Asset is outside the viewer folder scope."})
    cache_key = (str(tenant_id), str(resolved_source_id), str(item_id))
    cached = location_breadcrumb_cache.get(cache_key)
    if cached is not None:
        return AssetLocationResponse(status="available", breadcrumb=cached)
    source = session.scalar(select(SourceAssetModel).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.external_source_id == resolved_source_id, SourceAssetModel.external_asset_id == item_id, SourceAssetModel.deleted_at.is_(None)))
    external = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == tenant_id, ExternalSourceModel.id == resolved_source_id))
    if source is None or external is None:
        return AssetLocationResponse(status="unavailable", breadcrumb=[])
    rows = list(session.scalars(select(SourceAssetModel).where(SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.external_source_id == resolved_source_id, SourceAssetModel.deleted_at.is_(None))))
    folders = {}
    for row in rows:
        metadata = row.source_metadata if isinstance(row.source_metadata, dict) else {}
        parents = metadata.get("parents") if isinstance(metadata.get("parents"), list) else [metadata.get("parent_id")]
        folders[str(row.external_asset_id)] = {"name": row.filename or "Folder", "parent_id": next((str(value) for value in parents if value), None)}
    source_metadata = external.source_metadata if isinstance(external.source_metadata, dict) else {}
    root_id = str(source_metadata.get("root_folder_id") or source_metadata.get("folder_id") or "root")
    folders.setdefault("root", {"name": external.display_name or "My Drive", "parent_id": None})
    item_metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
    parent_id = next((str(value) for value in item_metadata.get("parents", []) if value), None) if isinstance(item_metadata.get("parents"), list) else str(item_metadata.get("parent_id") or "")
    permitted = set(access.folder_ids) if access.restricted else None
    breadcrumb = resolve_breadcrumb(item_id=item_id, parent_id=parent_id, folders=folders, source_root_id=root_id, permitted_root_ids=permitted)
    resolution_source = "database"
    failure_reason = "missing_parent"
    if not breadcrumb and provider == "google-drive" and token:
        resolution_source = "provider"
        provider_folders = {}
        current = parent_id
        visited = set()
        try:
            async with create_source_provider(provider, token) as client:
                if not current:
                    item_node = await client.get_node(item_id)
                    current = item_node.parent_id
                    parent_id = str(current or "")
                    if current:
                        item_source_metadata = dict(source.source_metadata or {})
                        item_source_metadata["parents"] = [str(current)]
                        source.source_metadata = item_source_metadata
                for depth in range(64):
                    if not current:
                        failure_reason = "missing_parent"
                        break
                    if current in visited:
                        failure_reason = "cycle_detected"
                        break
                    visited.add(current)
                    node = await client.get_node(current)
                    provider_folders[current] = {"name": node.name, "parent_id": node.parent_id}
                    if access.restricted and current in access.folder_ids:
                        failure_reason = ""
                        break
                    if current == root_id or (root_id == "root" and node.parent_id in {None, "root"}):
                        if root_id == "root":
                            root_id = current
                        failure_reason = ""
                        break
                    current = node.parent_id
                else:
                    failure_reason = "root_not_reached"
            breadcrumb = resolve_breadcrumb(item_id=item_id, parent_id=parent_id, folders={**folders, **provider_folders}, source_root_id=root_id, permitted_root_ids=permitted)
            if breadcrumb:
                from app.modules.assets.repository import AssetRegistryRepository
                repository = AssetRegistryRepository(session)
                for folder_id, folder in provider_folders.items():
                    repository.upsert_source_asset(
                        tenant_id=tenant_id, external_source_id=resolved_source_id,
                        external_asset_id=folder_id, filename=str(folder["name"]),
                        mime_type="application/vnd.google-apps.folder",
                        source_metadata={"parents": [folder["parent_id"]] if folder["parent_id"] else [], "is_folder": True},
                    )
                location_breadcrumb_cache.put(cache_key, breadcrumb)
                session.commit()
        except httpx.HTTPStatusError as exc:
            failure_reason = "provider_forbidden" if exc.response.status_code in {401, 403} else "provider_not_found" if exc.response.status_code == 404 else "provider_error"
        except (httpx.HTTPError, ValueError):
            failure_reason = "provider_error"
    logger.info("asset_location item_id=%s external_source_id=%s missing_parent_id=%s resolved_depth=%s resolution_source=%s failure_reason=%s", item_id, resolved_source_id, parent_id, len(breadcrumb), resolution_source, failure_reason)
    if breadcrumb:
        location_breadcrumb_cache.put(cache_key, breadcrumb)
        return AssetLocationResponse(status="available", breadcrumb=breadcrumb)
    return AssetLocationResponse(status="unavailable", breadcrumb=[])


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
        return await ExplorerService(
            create_source_provider, viewer_access=access,
        ).list_folders(
            parent_id,
            token,
            provider,
            viewer_parent_authorized=parent_id != "root",
            tenant_id=tenant_id,
            external_source_id=resolved_source_id,
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
    _require_legacy_admin(principal)
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
    _require_legacy_admin(principal)
    return get_index_status(principal.active_tenant_id, provider)


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


async def _resolve_note_owner(
    request: Request, folder_id: str, provider: Provider, session: Session,
    principal: CurrentPrincipal, external_source_id: str | None, require_write: bool = False,
) -> tuple[str, str, object, object | None]:
    if provider != "google-drive":
        raise HTTPException(status_code=501, detail="Folder notes are supported for Google Drive only.")
    token, _account, tenant_id, source_id = await _source_context(
        request, provider, session, principal, external_source_id,
        require_drive_write_scope=require_write,
    )
    if not token:
        raise HTTPException(status_code=401, detail="Connect Google Drive before using folder notes.")
    scope_service = ViewerFolderScopeService(session)
    access = scope_service.access(tenant_id=tenant_id, membership_id=principal.membership_id,
        roles=principal.effective_roles, external_source_id=source_id)
    async with create_source_provider(provider, token) as client:
        current = await client.get_node(folder_id)
        if current.kind != "folder":
            raise HTTPException(status_code=422, detail="Folder notes can only be opened from a folder.")
        _require_viewer_folder_scope(scope_service, tenant_id=tenant_id, access=access,
            folder_id=folder_id, allow_root=False)
        owner = await resolve_note_owner_from_nodes(current, client.get_node)
    if owner:
        _require_viewer_folder_scope(scope_service, tenant_id=tenant_id, access=access,
            folder_id=owner.id, allow_root=False)
    return tenant_id, source_id or "", current, owner


def _folder_note_response(requested_folder_id: str, owner: object | None, note: FolderNoteModel | None) -> FolderNoteResponse:
    owner_id = getattr(owner, "id", None)
    return FolderNoteResponse(
        requested_folder_id=requested_folder_id,
        note_owner_folder_id=owner_id,
        note_owner_folder_name=getattr(owner, "name", None),
        is_inherited=bool(owner_id and owner_id != requested_folder_id),
        content_markdown=note.content_markdown if note else "",
        updated_at=note.updated_at if note else None, updated_by=note.updated_by if note else None,
    )


@router.get("/folders/{folder_id}/note", response_model=FolderNoteResponse)
async def get_folder_note(
    request: Request, folder_id: str, provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db), principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    tenant_id, source_id, _current, owner = await _resolve_note_owner(
        request, folder_id, provider, session, principal, external_source_id)
    if owner is None:
        return _folder_note_response(folder_id, None, None)
    note = session.scalar(select(FolderNoteModel).where(
        FolderNoteModel.tenant_id == tenant_id, FolderNoteModel.external_source_id == source_id,
        FolderNoteModel.folder_external_id == owner.id))
    return _folder_note_response(folder_id, owner, note)


@router.put("/folders/{folder_id}/note", response_model=FolderNoteResponse)
async def put_folder_note(
    request: Request, folder_id: str, body: FolderNoteUpdateRequest,
    provider: Provider = Query("google-drive"), session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    tenant_id, source_id, _current, owner = await _resolve_note_owner(
        request, folder_id, provider, session, principal, external_source_id, require_write=True)
    if owner is None:
        raise HTTPException(status_code=422, detail="Folder is not inside a supported product folder.")
    note = session.scalar(select(FolderNoteModel).where(
        FolderNoteModel.tenant_id == tenant_id, FolderNoteModel.external_source_id == source_id,
        FolderNoteModel.folder_external_id == owner.id))
    content = body.content_markdown
    if not content.strip():
        if note:
            session.delete(note)
            session.commit()
        return _folder_note_response(folder_id, owner, None)
    if note is None:
        note = FolderNoteModel(tenant_id=tenant_id, external_source_id=source_id,
            folder_external_id=owner.id, content_markdown=content, updated_by=principal.actor_id)
        session.add(note)
    else:
        note.content_markdown = content
        note.updated_by = principal.actor_id
    session.commit()
    session.refresh(note)
    return _folder_note_response(folder_id, owner, note)


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


@router.post("/folders")
async def create_folder(
    request: Request,
    name: str = Query(..., min_length=1, max_length=255),
    parent_id: str = Query("root"),
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive":
        raise HTTPException(status_code=501, detail="Folder creation is not supported for this provider yet.")
    token, _account, tenant_id, source_id = await _source_context(
        request, provider, session, principal, external_source_id, require_drive_write_scope=True,
    )
    if not token:
        raise HTTPException(status_code=401, detail="Connect Google Drive before creating folders.")
    scope_service = ViewerFolderScopeService(session)
    access = scope_service.access(
        tenant_id=tenant_id, membership_id=principal.membership_id,
        roles=principal.effective_roles, external_source_id=source_id,
    )
    _require_viewer_folder_scope(
        scope_service, tenant_id=tenant_id, access=access, folder_id=parent_id, allow_root=False,
    )
    try:
        async with create_source_provider(provider, token) as client:
            parent = await client.get_node(parent_id)
            if parent.kind != "folder":
                raise HTTPException(status_code=422, detail="Destination must be a folder.")
            node = await client.create_folder(parent_id, name.strip())
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise _provider_error(exc, "Google Drive could not create the folder.") from exc
    viewer_folder_hierarchy_cache.invalidate(tenant_id=tenant_id, external_source_id=source_id)
    return {"id": node.id, "name": node.name, "kind": node.kind}


@router.put("/items/{item_id}/content")
async def update_text_file(
    request: Request,
    item_id: str,
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
    external_source_id: str | None = Query(None),
):
    if provider != "google-drive":
        raise HTTPException(status_code=501, detail="Text editing is not supported for this provider yet.")
    content = await request.body()
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Text files larger than 1 MB cannot be edited here.")
    token, _account, tenant_id, source_id = await _source_context(
        request, provider, session, principal, external_source_id, require_drive_write_scope=True,
    )
    if not token:
        raise HTTPException(status_code=401, detail="Connect Google Drive before editing text files.")
    scope_service = ViewerFolderScopeService(session)
    access = scope_service.access(
        tenant_id=tenant_id, membership_id=principal.membership_id,
        roles=principal.effective_roles, external_source_id=source_id,
    )
    try:
        async with create_source_provider(provider, token) as client:
            current = await client.get_node(item_id)
            is_text = (
                (current.mime_type or "").split(";", 1)[0].lower() == "text/plain"
                or current.name.lower().endswith(".txt")
            )
            if current.kind == "folder" or not is_text:
                raise HTTPException(status_code=422, detail="Only plain-text TXT files can be edited.")
            _require_viewer_folder_scope(
                scope_service, tenant_id=tenant_id, access=access,
                folder_id=current.parent_id or "root", allow_root=False,
            )
            node = await client.update_file_content(item_id, current.name, "text/plain", content)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise _provider_error(exc, "Google Drive could not update the text file.") from exc
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
    location_breadcrumb_cache.invalidate(tenant_id=tenant_id, external_source_id=resolved_source_id, item_id=item_id)
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
    location_breadcrumb_cache.invalidate(tenant_id=tenant_id, external_source_id=resolved_source_id, item_id=item_id)
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
    location_breadcrumb_cache.invalidate(tenant_id=tenant_id, external_source_id=resolved_source_id, item_id=item_id)
    return {"id": node.id, "parent_id": node.parent_id}

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


@router.get("/preview/{item_id}")
async def preview(
    request: Request,
    item_id: str,
    provider: Provider = Query("google-drive"),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(ASSETS_READ),
    external_source_id: str | None = Query(None),
):
    token, tenant_id, resolved_source_id = await _authorized_file_context(
        request, item_id, provider, session, principal, external_source_id
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
    if infer_media_type(filename, declared_mime) != "image/avif":
        raise HTTPException(status_code=415, detail={
            "code": "preview_unsupported_media",
            "message": "The preview endpoint only converts AVIF images.",
            "retryable": False,
        })

    settings = get_settings()
    shared_client = getattr(request.app.state, "google_drive_stream_client", None)
    client = None
    upstream = None
    close_client = True
    try:
        if provider == "sharepoint":
            client, upstream = await open_sharepoint_media(token, item_id, None)
        else:
            client, upstream = await open_google_media(
                token, item_id, None, http_client=shared_client,
            )
            close_client = client is not shared_client
        declared_size = upstream.headers.get("content-length")
        if declared_size and int(declared_size) > settings.AVIF_PREVIEW_MAX_INPUT_BYTES:
            raise HTTPException(status_code=413, detail={
                "code": "avif_preview_input_too_large",
                "message": "The AVIF image exceeds the preview size limit.",
                "retryable": False,
            })
        etag = upstream.headers.get("etag") or ""
        modified = upstream.headers.get("last-modified") or ""
        cache_key = (str(tenant_id), str(resolved_source_id), item_id, etag + "|" + modified)
        cached = preview_cache_get(cache_key)
        if cached is None:
            content = bytearray()
            async for chunk in upstream.aiter_bytes():
                content.extend(chunk)
                if len(content) > settings.AVIF_PREVIEW_MAX_INPUT_BYTES:
                    raise HTTPException(status_code=413, detail={
                        "code": "avif_preview_input_too_large",
                        "message": "The AVIF image exceeds the preview size limit.",
                        "retryable": False,
                    })
            try:
                cached = await asyncio.to_thread(convert_avif_to_webp, bytes(content))
            except PreviewConversionError as exc:
                raise HTTPException(status_code=422, detail={
                    "code": "avif_preview_conversion_failed",
                    "message": str(exc),
                    "retryable": False,
                }) from exc
            preview_cache_put(cache_key, cached)
        return Response(
            content=cached,
            media_type="image/webp",
            headers={
                "content-disposition": "inline",
                "cache-control": "private, max-age=3600",
                "x-content-type-options": "nosniff",
            },
        )
    except HTTPException:
        raise
    except (httpx.HTTPError, PermissionError, ValueError) as exc:
        raise _provider_error(exc, "Unable to download AVIF preview source") from exc
    finally:
        if client is not None and upstream is not None:
            if provider == "sharepoint":
                await close_sharepoint_media(client, upstream)
            else:
                await close_google_media(client, upstream, close_client)


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
        passthrough_headers["content-disposition"] = "inline"

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
