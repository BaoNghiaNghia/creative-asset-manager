from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domain.processing.handlers import JobHandlerResult
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.video_search.elasticsearch import VideoSearchElasticsearchIndex
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


@router.post("/video")
async def video_search(
    body: VideoSearchRequest,
    principal: CurrentPrincipal = Depends(VIDEO_SEARCH_READ),
):
    query = " ".join(body.query.split())
    if not query:
        raise HTTPException(422, detail={"code": "invalid_video_search_query", "message": "A video search query is required."})
    settings = get_settings()
    if not (
        settings.VIDEO_SEARCH_ENABLED
        and settings.ELASTICSEARCH_V2_ENABLED
        and settings.ELASTICSEARCH_URL
    ):
        raise HTTPException(503, detail={"code": "video_search_unavailable", "message": "Video search is unavailable.", "retryable": True})
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
        ))
        return parse_video_search_response(response)
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(503, detail={"code": "video_search_unavailable", "message": "Video search is temporarily unavailable.", "retryable": True}) from exc
    except VideoSearchResponseError as exc:
        raise HTTPException(502, detail={"code": "video_search_response_invalid", "message": "Video search returned an invalid response.", "retryable": True}) from exc
    finally:
        await index.aclose()
