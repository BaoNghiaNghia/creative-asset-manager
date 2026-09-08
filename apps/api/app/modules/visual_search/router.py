from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.assets.content_resolver import SourceAssetContentTransient, SourceAssetContentUnavailable
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.authorization.principal import CurrentPrincipal, require_permission, is_pure_viewer
from app.modules.search.router import _hydrate_search_hits, _search_scope_filters, _typed_filters
from app.modules.search.schema import SearchCoreFilters
from app.modules.visual_search.contracts import VisualEmbedding, VisualEncoderUnavailableError
from app.modules.visual_search.elasticsearch import VisualSearchElasticsearchIndex, VisualSearchScope
from app.modules.visual_search.encoder import SiglipVisualEncoder
from app.modules.visual_search.schema import NormalizedCrop, VisualSearchByAssetRequest, VisualSearchResponse
from app.modules.visual_search.preprocess import VisualImagePreparationError, VisualPreprocessLimits, decode_visual_image
from app.modules.visual_search.ranking import VisualRankingWeights, diversify_hits, fuse_embeddings
from app.modules.visual_search.service import VisualSearchDisabledError, VisualSearchService

router = APIRouter(prefix="/api/v1/search/visual", tags=["visual-search"])
VISUAL_SEARCH_READ = require_permission("search.read")
_MAX_CANDIDATES = 100
_UPLOAD_READ_CHUNK_BYTES = 1_048_576
_ENCODER_CAPACITY = asyncio.Semaphore(1)
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


def _ranking_weights(settings) -> VisualRankingWeights:
    return VisualRankingWeights(
        image=settings.VISUAL_SEARCH_RANKING_IMAGE_WEIGHT,
        text=settings.VISUAL_SEARCH_RANKING_TEXT_WEIGHT,
        max_per_source=settings.VISUAL_SEARCH_RANKING_MAX_PER_SOURCE,
    )


async def _text_embedding(request: Request, text: str) -> VisualEmbedding:
    value = text.strip()
    if not value:
        raise HTTPException(422, detail={"code": "visual_refinement_invalid", "message": "Text refinement is required."})
    if _ENCODER_CAPACITY.locked():
        raise HTTPException(503, detail={"code": "visual_encoder_capacity", "message": "Visual search is busy. Please retry shortly.", "retryable": True})
    await _ENCODER_CAPACITY.acquire()
    try:
        client = getattr(request.app.state, "visual_encoder_client", None)
        encoder = client.get_encoder() if client is not None and callable(getattr(client, "get_encoder", None)) else None
        if encoder is None or not callable(getattr(encoder, "encode_text", None)):
            raise HTTPException(503, detail={"code": "visual_encoder_unavailable", "message": "Visual search text refinement is temporarily unavailable.", "retryable": True})
        try:
            return await asyncio.to_thread(encoder.encode_text, value)
        except (VisualEncoderUnavailableError, ValueError) as exc:
            raise HTTPException(503, detail={"code": "visual_encoder_unavailable", "message": "Visual search text refinement is temporarily unavailable.", "retryable": True}) from exc
    finally:
        _ENCODER_CAPACITY.release()


def _rank_hits(hits, *, offset: int, limit: int, settings):
    ranked = diversify_hits(hits, weights=_ranking_weights(settings))
    return ranked[offset: offset + limit + 1], len(ranked)


@router.post("/by-asset", response_model=VisualSearchResponse)
async def find_similar_by_asset(
    request: Request,
    body: VisualSearchByAssetRequest,
    principal: CurrentPrincipal = Depends(VISUAL_SEARCH_READ),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        service = VisualSearchService(settings)
        service.require_operation("crop" if body.crop is not None else "asset")
        if body.text is not None:
            service.require_operation("hybrid_text")
    except VisualSearchDisabledError as exc:
        raise HTTPException(503, detail={"code": exc.code, "message": "Visual search is disabled.", "retryable": False}) from exc
    if not settings.ELASTICSEARCH_URL:
        raise HTTPException(503, detail={"code": "visual_search_unavailable", "message": "Visual search is temporarily unavailable.", "retryable": True})
    if is_pure_viewer(principal) and not (body.external_source_id or "").strip():
        raise HTTPException(422, detail={"code": "viewer_source_required", "message": "A search source is required for scoped Viewer search."})
    if body.crop is not None:
        return await _find_similar_by_asset_crop(request, body, principal, settings)

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
        "text": body.text,
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
        if body.text is not None:
            embedding = fuse_embeddings(
                embedding,
                await _text_embedding(request, body.text),
                weights=_ranking_weights(settings),
            )
        scope = VisualSearchScope(tenant, tuple([*filters, *_typed_filters(body.filters)]))
        hits = await index.search(embedding, scope=scope, limit=_MAX_CANDIDATES, num_candidates=_MAX_CANDIDATES, exclude_asset_id=asset.id)
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(503, detail={"code": "visual_search_unavailable", "message": "Visual search is temporarily unavailable.", "retryable": True}) from exc
    finally:
        await index.aclose()

    candidates, ranked_count = _rank_hits(hits, offset=offset, limit=body.limit, settings=settings)
    raw_hits = [{"_id": hit.document_id, "_score": hit.score, "_source": {"asset_id": hit.asset_id, "source_id": hit.source_id}} for hit in candidates[:body.limit]]
    with SessionLocal() as session:
        items = _hydrate_search_hits(session, tenant, raw_hits, viewer_restricted=viewer_restricted, limit=body.limit)
        session.commit()
    for item in items:
        item.pop("score", None)
    cursor = _next_cursor(offset, ranked_count - offset, body.limit, fingerprint=fingerprint)
    logger.info(
        "visual_search_by_asset_completed tenant_id=%s result_count=%s duration_ms=%s",
        tenant,
        len(items),
        round((time.monotonic() - started) * 1000),
    )
    return {"query_kind": "asset", "items": items, "next_cursor": cursor, "has_more": cursor is not None}


def _parse_crop(value: str | None) -> NormalizedCrop | None:
    if not value:
        return None
    try:
        return NormalizedCrop.model_validate_json(value)
    except ValidationError as exc:
        raise HTTPException(
            422,
            detail={"code": "visual_crop_invalid", "message": "Visual-search crop is invalid."},
        ) from exc


def _parse_upload_filters(value: str | None) -> SearchCoreFilters:
    if not value:
        return SearchCoreFilters()
    try:
        return SearchCoreFilters.model_validate_json(value)
    except ValidationError as exc:
        raise HTTPException(
            422,
            detail={
                "code": "visual_filters_invalid",
                "message": "Visual-search filters are invalid.",
            },
        ) from exc


async def _read_upload_bytes(file: UploadFile) -> bytes:
    limit = VisualPreprocessLimits().max_source_bytes
    parts: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                413,
                detail={
                    "code": "visual_upload_too_large",
                    "message": "Visual-search image exceeds the byte limit.",
                },
            )
        parts.append(chunk)
    return b"".join(parts)


async def _upload_embedding(
    request: Request,
    content: bytes,
    *,
    crop: NormalizedCrop | None = None,
) -> VisualEmbedding:
    if _ENCODER_CAPACITY.locked():
        raise HTTPException(
            503,
            detail={
                "code": "visual_encoder_capacity",
                "message": "Visual search is busy. Please retry shortly.",
                "retryable": True,
            },
        )
    await _ENCODER_CAPACITY.acquire()
    try:
        prepared = await asyncio.to_thread(decode_visual_image, content, crop=crop)
        client = getattr(request.app.state, "visual_encoder_client", None)
        if client is None or not callable(getattr(client, "get_encoder", None)):
            raise HTTPException(
                503,
                detail={
                    "code": "visual_encoder_unavailable",
                    "message": "Visual search is temporarily unavailable.",
                    "retryable": True,
                },
            )
        try:
            embedding = await asyncio.to_thread(
                lambda: client.get_encoder().encode_image(prepared.image)
            )
        except VisualEncoderUnavailableError as exc:
            raise HTTPException(
                503,
                detail={
                    "code": "visual_encoder_unavailable",
                    "message": "Visual search is temporarily unavailable.",
                    "retryable": True,
                },
            ) from exc
        except Exception as exc:
            logger.warning("visual_encoder_request_failed error_type=%s", type(exc).__name__)
            raise HTTPException(
                503,
                detail={
                    "code": "visual_encoder_unavailable",
                    "message": "Visual search is temporarily unavailable.",
                    "retryable": True,
                },
            ) from exc
        if embedding.descriptor != SiglipVisualEncoder.descriptor:
            raise HTTPException(
                503,
                detail={
                    "code": "visual_encoder_unavailable",
                    "message": "Visual search is temporarily unavailable.",
                    "retryable": True,
                },
            )
        return embedding
    except VisualImagePreparationError as exc:
        status_code = 413 if exc.code in {
            "visual_image_too_large",
            "visual_image_dimensions",
            "visual_image_decode_pixels",
        } else 422
        raise HTTPException(
            status_code,
            detail={"code": exc.code, "message": str(exc), "retryable": False},
        ) from exc
    finally:
        _ENCODER_CAPACITY.release()


@router.post("/upload", response_model=VisualSearchResponse)
async def find_similar_by_upload(
    request: Request,
    file: UploadFile = File(...),
    source_provider: Literal["google-drive", "onedrive", "sharepoint"] | None = Query(None),
    external_source_id: str | None = Query(default=None, max_length=128),
    filters: str | None = Query(default=None, max_length=4096),
    crop: str | None = Query(default=None, max_length=512),
    text: str | None = Query(default=None, max_length=500),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=40, ge=1, le=100),
    principal: CurrentPrincipal = Depends(VISUAL_SEARCH_READ),
) -> dict[str, Any]:
    settings = get_settings()
    parsed_crop = _parse_crop(crop)
    text = (text or "").strip() or None
    try:
        service = VisualSearchService(settings)
        service.require_operation("crop" if parsed_crop is not None else "upload")
        if text is not None:
            service.require_operation("hybrid_text")
    except VisualSearchDisabledError as exc:
        raise HTTPException(
            503,
            detail={
                "code": exc.code,
                "message": "Visual-search uploads are disabled.",
                "retryable": False,
            },
        ) from exc
    if not settings.ELASTICSEARCH_URL:
        raise HTTPException(
            503,
            detail={
                "code": "visual_search_unavailable",
                "message": "Visual search is temporarily unavailable.",
                "retryable": True,
            },
        )
    if is_pure_viewer(principal) and not (external_source_id or "").strip():
        raise HTTPException(
            422,
            detail={
                "code": "viewer_source_required",
                "message": "A search source is required for scoped Viewer search.",
            },
        )

    try:
        content = await _read_upload_bytes(file)
    finally:
        await file.close()
    embedding = await _upload_embedding(request, content, crop=parsed_crop)
    if text is not None:
        embedding = fuse_embeddings(
            embedding,
            await _text_embedding(request, text),
            weights=_ranking_weights(settings),
        )
    parsed_filters = _parse_upload_filters(filters)
    tenant = principal.active_tenant_id
    started = time.monotonic()
    with SessionLocal() as session:
        access_filters, viewer_scope_key, viewer_restricted = _search_scope_filters(
            session,
            principal,
            source_provider=source_provider,
            external_source_id=external_source_id,
        )
        session.commit()

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "tenant": tenant,
                "content": hashlib.sha256(content).hexdigest(),
                "text": text,
                "filters": parsed_filters.model_dump(mode="json", exclude_none=True),
                "source_provider": source_provider,
                "external_source_id": external_source_id,
                "viewer": viewer_scope_key,
                "schema": embedding.descriptor.embedding_schema_version,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    offset = _cursor_offset(cursor, fingerprint=fingerprint)
    search_limit = min(_MAX_CANDIDATES, offset + limit + 1)
    index = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config(
            settings.ELASTICSEARCH_URL,
            settings.ELASTICSEARCH_INDEX_PREFIX,
            index_generation="v3",
        ),
        embedding.descriptor,
    )
    try:
        scope = VisualSearchScope(
            tenant,
            tuple([*access_filters, *_typed_filters(parsed_filters)]),
        )
        hits = await index.search(
            embedding,
            scope=scope,
            limit=_MAX_CANDIDATES,
            num_candidates=_MAX_CANDIDATES,
        )
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "visual_search_unavailable",
                "message": "Visual search is temporarily unavailable.",
                "retryable": True,
            },
        ) from exc
    finally:
        await index.aclose()

    candidates, ranked_count = _rank_hits(hits, offset=offset, limit=limit, settings=settings)
    raw_hits = [
        {
            "_id": hit.document_id,
            "_score": hit.score,
            "_source": {"asset_id": hit.asset_id, "source_id": hit.source_id},
        }
        for hit in candidates[:limit]
    ]
    with SessionLocal() as session:
        items = _hydrate_search_hits(
            session,
            tenant,
            raw_hits,
            viewer_restricted=viewer_restricted,
            limit=limit,
        )
        session.commit()
    for item in items:
        item.pop("score", None)
    next_cursor = _next_cursor(offset, ranked_count - offset, limit, fingerprint=fingerprint)
    logger.info(
        "visual_search_upload_completed tenant_id=%s result_count=%s duration_ms=%s",
        tenant,
        len(items),
        round((time.monotonic() - started) * 1000),
    )
    return {
        "query_kind": "upload",
        "items": items,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }



async def _read_authorized_source_content(
    request: Request,
    *,
    tenant_id: str,
    source_asset_id: str,
    expected_sha256: str,
) -> bytes:
    resolver = getattr(request.app.state, "visual_content_resolver", None)
    if resolver is None or not callable(getattr(resolver, "open", None)):
        raise HTTPException(
            503,
            detail={
                "code": "visual_source_unavailable",
                "message": "Visual search is temporarily unavailable.",
                "retryable": True,
            },
        )
    parts: list[bytes] = []
    total = 0
    limit = VisualPreprocessLimits().max_source_bytes
    try:
        async with resolver.open(tenant_id=tenant_id, source_asset_id=source_asset_id) as stream:
            async for chunk in stream.body:
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        413,
                        detail={
                            "code": "visual_image_too_large",
                            "message": "Visual-search image exceeds the byte limit.",
                            "retryable": False,
                        },
                    )
                parts.append(chunk)
    except HTTPException:
        raise
    except (SourceAssetContentTransient, SourceAssetContentUnavailable) as exc:
        raise HTTPException(
            503,
            detail={
                "code": "visual_source_unavailable",
                "message": "Visual search is temporarily unavailable.",
                "retryable": True,
            },
        ) from exc
    content = b"".join(parts)
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise HTTPException(
            409,
            detail={
                "code": "visual_query_asset_stale",
                "message": "Asset content changed before visual search.",
                "retryable": True,
            },
        )
    return content


async def _find_similar_by_asset_crop(
    request: Request,
    body: VisualSearchByAssetRequest,
    principal: CurrentPrincipal,
    settings,
) -> dict[str, Any]:
    tenant = principal.active_tenant_id
    with SessionLocal() as session:
        asset = session.scalar(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant,
                AssetModel.id == body.asset_id,
            )
        )
        if asset is None:
            raise HTTPException(
                404,
                detail={
                    "code": "visual_query_asset_not_found",
                    "message": "Asset is unavailable.",
                },
            )
        access_filters, viewer_scope_key, viewer_restricted = _search_scope_filters(
            session,
            principal,
            source_provider=body.source_provider,
            external_source_id=body.external_source_id,
        )
        source_query = (
            select(SourceAssetModel)
            .join(
                AssetSourceLinkModel,
                AssetSourceLinkModel.source_asset_id == SourceAssetModel.id,
            )
            .where(
                AssetSourceLinkModel.tenant_id == tenant,
                AssetSourceLinkModel.asset_id == asset.id,
                SourceAssetModel.tenant_id == tenant,
                SourceAssetModel.deleted_at.is_(None),
            )
            .order_by(SourceAssetModel.id)
        )
        if body.external_source_id:
            source_query = source_query.where(
                SourceAssetModel.external_source_id == body.external_source_id
            )
        source = session.scalar(source_query)
        if viewer_restricted:
            access = ViewerFolderScopeService(session).access(
                tenant_id=tenant,
                membership_id=principal.membership_id,
                roles=principal.effective_roles,
                external_source_id=body.external_source_id,
            )
            allowed_pairs = ViewerFolderScopeService(session).allowed_asset_source_pairs(
                tenant_id=tenant,
                access=access,
            )
            if source is None or (asset.id, source.id) not in allowed_pairs:
                source = next(
                    (
                        row
                        for row in session.scalars(source_query)
                        if (asset.id, row.id) in allowed_pairs
                    ),
                    None,
                )
        session.commit()
    if source is None:
        raise HTTPException(
            404,
            detail={
                "code": "visual_query_asset_not_found",
                "message": "Asset is unavailable.",
            },
        )

    content = await _read_authorized_source_content(
        request,
        tenant_id=tenant,
        source_asset_id=source.id,
        expected_sha256=asset.content_hash,
    )
    embedding = await _upload_embedding(request, content, crop=body.crop)
    if body.text is not None:
        embedding = fuse_embeddings(
            embedding,
            await _text_embedding(request, body.text),
            weights=_ranking_weights(settings),
        )
    started = time.monotonic()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "tenant": tenant,
                "asset": asset.id,
                "hash": asset.content_hash,
                "crop": body.crop.model_dump(mode="json"),
                "text": body.text,
                "filters": body.filters.model_dump(mode="json", exclude_none=True),
                "source_provider": body.source_provider,
                "external_source_id": body.external_source_id,
                "viewer": viewer_scope_key,
                "schema": embedding.descriptor.embedding_schema_version,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    offset = _cursor_offset(body.cursor, fingerprint=fingerprint)
    search_limit = min(_MAX_CANDIDATES, offset + body.limit + 1)
    index = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config(
            settings.ELASTICSEARCH_URL,
            settings.ELASTICSEARCH_INDEX_PREFIX,
            index_generation="v3",
        ),
        embedding.descriptor,
    )
    try:
        scope = VisualSearchScope(
            tenant,
            tuple([*access_filters, *_typed_filters(body.filters)]),
        )
        hits = await index.search(
            embedding,
            scope=scope,
            limit=_MAX_CANDIDATES,
            num_candidates=_MAX_CANDIDATES,
            exclude_asset_id=asset.id,
        )
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(
            503,
            detail={
                "code": "visual_search_unavailable",
                "message": "Visual search is temporarily unavailable.",
                "retryable": True,
            },
        ) from exc
    finally:
        await index.aclose()

    candidates, ranked_count = _rank_hits(hits, offset=offset, limit=body.limit, settings=settings)
    raw_hits = [
        {
            "_id": hit.document_id,
            "_score": hit.score,
            "_source": {"asset_id": hit.asset_id, "source_id": hit.source_id},
        }
        for hit in candidates[:body.limit]
    ]
    with SessionLocal() as session:
        items = _hydrate_search_hits(
            session,
            tenant,
            raw_hits,
            viewer_restricted=viewer_restricted,
            limit=body.limit,
        )
        session.commit()
    for item in items:
        item.pop("score", None)
    next_cursor = _next_cursor(
        offset,
        ranked_count - offset,
        body.limit,
        fingerprint=fingerprint,
    )
    logger.info(
        "visual_search_asset_crop_completed tenant_id=%s result_count=%s duration_ms=%s",
        tenant,
        len(items),
        round((time.monotonic() - started) * 1000),
    )
    return {
        "query_kind": "asset",
        "items": items,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }
