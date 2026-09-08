from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.assets.model import AssetModel
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.authorization.principal import CurrentPrincipal, require_permission, is_pure_viewer
from app.modules.search.router import _hydrate_search_hits, _search_scope_filters, _typed_filters
from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualSearchElasticsearchIndex, VisualSearchScope
from app.modules.visual_search.encoder import SiglipVisualEncoder
from app.modules.visual_search.schema import VisualSearchByAssetRequest, VisualSearchResponse
from app.modules.visual_search.service import VisualSearchDisabledError, VisualSearchService

router = APIRouter(prefix="/api/v1/search/visual", tags=["visual-search"])
VISUAL_SEARCH_READ = require_permission("search.read")
_MAX_CANDIDATES = 100
logger = logging.getLogger(__name__)


def _document_id(tenant_id: str, asset_id: str, content_sha256: str, schema: str) -> str:
    value = json.dumps((tenant_id, asset_id, content_sha256, schema), separators=(",", ":"), ensure_ascii=True)
    return "visual:" + hashlib.sha256(value.encode()).hexdigest()


def _cursor_offset(cursor: str | None, *, fingerprint: str) -> int:
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if payload["f"] != fingerprint:
            raise ValueError
        offset = int(payload["o"])
        if offset < 0 or offset >= _MAX_CANDIDATES:
            raise ValueError
        return offset
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, detail={"code": "visual_cursor_invalid", "message": "Invalid visual-search cursor."}) from exc


def _next_cursor(offset: int, count: int, limit: int, *, fingerprint: str) -> str | None:
    if count < limit or offset + count >= _MAX_CANDIDATES:
        return None
    return base64.urlsafe_b64encode(json.dumps({"f": fingerprint, "o": offset + count}, separators=(",", ":")).encode()).decode()


@router.post("/by-asset", response_model=VisualSearchResponse)
async def find_similar_by_asset(
    body: VisualSearchByAssetRequest,
    principal: CurrentPrincipal = Depends(VISUAL_SEARCH_READ),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        VisualSearchService(settings).require_operation("asset")
    except VisualSearchDisabledError as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": "Visual search is disabled.", "retryable": False}) from exc
    if not settings.ELASTICSEARCH_URL:
        raise HTTPException(503, detail={"code": "visual_search_unavailable", "message": "Visual search is temporarily unavailable.", "retryable": True})
    if body.crop is not None or body.text is not None:
        raise HTTPException(422, detail={"code": "visual_search_refinement_unsupported", "message": "Crop and text refinement are not available yet."})
    if is_pure_viewer(principal) and not (body.external_source_id or "").strip():
        raise HTTPException(422, detail={"code": "viewer_source_required", "message": "A search source is required for scoped Viewer search."})

    tenant = principal.active_tenant_id
    started = time.monotonic()
    with SessionLocal() as session:
        asset = session.scalar(select(AssetModel).where(AssetModel.tenant_id == tenant, AssetModel.id == body.asset_id))
        if asset is None:
            raise HTTPException(404, detail={"code": "visual_query_asset_not_found", "message": "Asset is unavailable."})
        filters, viewer_scope_key, viewer_restricted = _search_scope_filters(
            session, principal, source_provider=body.source_provider, external_source_id=body.external_source_id,
        )
        if viewer_restricted:
            access = ViewerFolderScopeService(session).access(
                tenant_id=tenant,
                membership_id=principal.membership_id,
                roles=principal.effective_roles,
                external_source_id=body.external_source_id,
            )
            if asset.id not in ViewerFolderScopeService(session).allowed_internal_asset_ids(
                tenant_id=tenant, access=access,
            ):
                raise HTTPException(404, detail={"code": "visual_query_asset_not_found", "message": "Asset is unavailable."})
        session.commit()

    descriptor = SiglipVisualEncoder.descriptor
    document_id = _document_id(tenant, asset.id, asset.content_hash, descriptor.embedding_schema_version)
    fingerprint = hashlib.sha256(json.dumps({
        "tenant": tenant, "asset": asset.id, "hash": asset.content_hash,
        "filters": body.filters.model_dump(mode="json", exclude_none=True),
        "source_provider": body.source_provider, "external_source_id": body.external_source_id,
        "viewer": viewer_scope_key, "schema": descriptor.embedding_schema_version,
    }, sort_keys=True, default=str).encode()).hexdigest()
    offset = _cursor_offset(body.cursor, fingerprint=fingerprint)
    search_limit = min(_MAX_CANDIDATES, offset + body.limit + 1)
    index = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config(settings.ELASTICSEARCH_URL, settings.ELASTICSEARCH_INDEX_PREFIX, index_generation="v3"),
        descriptor,
    )
    try:
        try:
            document = await index.get_document(document_id)
        except ElasticsearchV3RequestError as exc:
            if exc.status_code == 404:
                raise HTTPException(409, detail={"code": "visual_query_embedding_pending", "message": "Visual embedding is not ready yet.", "retryable": True}) from exc
            raise
        source = document.get("_source", {})
        values = source.get("visual_embedding") if isinstance(source, dict) else None
        if not isinstance(values, list):
            raise HTTPException(409, detail={"code": "visual_query_embedding_pending", "message": "Visual embedding is not ready yet.", "retryable": True})
        try:
            embedding = VisualEmbedding(descriptor, tuple(float(value) for value in values))
        except (TypeError, ValueError) as exc:
            raise HTTPException(409, detail={"code": "visual_query_embedding_pending", "message": "Visual embedding is not ready yet.", "retryable": True}) from exc
        scope = VisualSearchScope(tenant, tuple([*filters, *_typed_filters(body.filters)]))
        hits = await index.search(embedding, scope=scope, limit=search_limit, num_candidates=_MAX_CANDIDATES, exclude_asset_id=asset.id)
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(503, detail={"code": "visual_search_unavailable", "message": "Visual search is temporarily unavailable.", "retryable": True}) from exc
    finally:
        await index.aclose()

    candidates = hits[offset: offset + body.limit + 1]
    raw_hits = [{"_id": hit.document_id, "_score": hit.score, "_source": {"asset_id": hit.asset_id, "source_id": hit.source_id}} for hit in candidates[:body.limit]]
    with SessionLocal() as session:
        items = _hydrate_search_hits(session, tenant, raw_hits, viewer_restricted=viewer_restricted, limit=body.limit)
        session.commit()
    for item in items:
        item.pop("score", None)
    cursor = _next_cursor(offset, len(candidates), body.limit, fingerprint=fingerprint)
    logger.info(
        "visual_search_by_asset_completed tenant_id=%s result_count=%s duration_ms=%s",
        tenant,
        len(items),
        round((time.monotonic() - started) * 1000),
    )
    return {"query_kind": "asset", "items": items, "next_cursor": cursor, "has_more": cursor is not None}
