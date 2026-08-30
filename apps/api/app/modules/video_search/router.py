from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.ai_operations.media_dashboard import MediaDashboardService
from app.modules.assets.model import ExternalSourceModel
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.authorization.principal import CurrentPrincipal, is_pure_viewer, require_permission
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex
from app.modules.search.schema import DesignType
from app.modules.video_search.search import (
    VideoSearchResponseError,
    build_video_search_query,
    parse_video_search_response,
)

router = APIRouter(prefix="/api/v1/search", tags=["video-search"])
VIDEO_SEARCH_READ = require_permission("search.read")


class VideoSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)
    external_source_id: str | None = Field(default=None, min_length=1, max_length=36)
    design_types: list[DesignType] = Field(default_factory=list, max_length=3)


def _authorized_video_scope(
    *,
    external_source_id: str | None,
    principal: CurrentPrincipal,
    session: Session,
) -> tuple[str | None, set[str] | None]:
    source_id = (external_source_id or "").strip() or None
    if is_pure_viewer(principal) and source_id is None:
        raise HTTPException(
            422,
            detail={
                "code": "viewer_source_context_required",
                "message": "Select a source before searching videos.",
            },
        )
    if source_id is None:
        return None, None
    source_exists = session.scalar(
        select(ExternalSourceModel.id).where(
            ExternalSourceModel.id == source_id,
            ExternalSourceModel.tenant_id == principal.active_tenant_id,
        )
    )
    if source_exists is None:
        raise HTTPException(
            403,
            detail={
                "code": "video_search_source_denied",
                "message": "The selected source is unavailable.",
            },
        )
    scope_service = ViewerFolderScopeService(session)
    access = scope_service.access(
        tenant_id=principal.active_tenant_id,
        membership_id=principal.membership_id,
        roles=principal.effective_roles,
        external_source_id=source_id,
    )
    return (
        source_id,
        scope_service.allowed_source_asset_ids(
            tenant_id=principal.active_tenant_id,
            access=access,
        )
        if access.restricted
        else None,
    )


@router.get("/video/{source_asset_id}")
def video_search_detail(
    source_asset_id: str,
    external_source_id: str | None = Query(default=None),
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(VIDEO_SEARCH_READ),
):
    authorized_source_id, allowed_source_asset_ids = _authorized_video_scope(
        external_source_id=external_source_id,
        principal=principal,
        session=session,
    )
    if allowed_source_asset_ids is not None and source_asset_id not in allowed_source_asset_ids:
        raise HTTPException(404, detail={"code": "video_not_found", "message": "Video is unavailable."})
    document = MediaDashboardService(session, get_settings()).video_detail(
        principal.active_tenant_id,
        source_asset_id,
    )
    if (
        document is None
        or (
            authorized_source_id is not None
            and document.get("external_source_id") != authorized_source_id
        )
    ):
        raise HTTPException(404, detail={"code": "video_not_found", "message": "Video is unavailable."})
    return document


@router.post("/video")
async def video_search(
    body: VideoSearchRequest,
    session: Session = Depends(get_db),
    principal: CurrentPrincipal = Depends(VIDEO_SEARCH_READ),
):
    query = " ".join(body.query.split())
    if not query:
        raise HTTPException(422, detail={"code": "invalid_video_search_query", "message": "A video search query is required."})
    settings = get_settings()
    if not (
        settings.VIDEO_SEARCH_ENABLED
        and settings.SEARCH_V3_ENABLED
        and settings.ELASTICSEARCH_URL
    ):
        raise HTTPException(503, detail={"code": "video_search_unavailable", "message": "Video search is unavailable.", "retryable": True})
    external_source_id, allowed_source_asset_ids = _authorized_video_scope(
        external_source_id=body.external_source_id,
        principal=principal,
        session=session,
    )
    index = VideoSearchElasticsearchIndex(
        ElasticsearchV3Config(
            settings.ELASTICSEARCH_URL,
            settings.ELASTICSEARCH_INDEX_PREFIX,
            index_generation="v3",
        )
    )
    try:
        response = await index.search(build_video_search_query(
            query=query,
            tenant_id=principal.active_tenant_id,
            limit=body.limit,
            external_source_id=external_source_id,
            allowed_source_asset_ids=allowed_source_asset_ids,
            design_types=body.design_types,
        ))
        return parse_video_search_response(response)
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(503, detail={"code": "video_search_unavailable", "message": "Video search is temporarily unavailable.", "retryable": True}) from exc
    except VideoSearchResponseError as exc:
        raise HTTPException(502, detail={"code": "video_search_response_invalid", "message": "Video search returned an invalid response.", "retryable": True}) from exc
    finally:
        await index.aclose()
