from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from urllib.parse import quote, urlencode
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.explorer.media_types import infer_media_type
from app.modules.search.runtime import API_SEARCH_INDEX_POOL, SEARCH_SUGGESTION_CACHE
from app.modules.ai_metadata.model import MetadataProfileModel
from app.modules.authorization.principal import CurrentPrincipal, require_permission, is_pure_viewer
from app.modules.authorization.folder_scope import ViewerFolderScopeService
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.assets.source_url import resolve_source_web_url
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.modules.processing_policy.service import ProcessingPolicyService
from app.modules.search.query_builder import (
    ElasticsearchQueryBuilder,
    SearchQueryConfig,
    decode_search_cursor,
    encode_search_cursor,
)
from app.modules.search.query_parser import SearchQueryParser
from app.modules.search.schema import SearchCapabilities, SearchSuggestionsResponse, SearchV3Request, SearchV3Response
from app.modules.search.governance_model import SearchIndexRecordModel

router = APIRouter(prefix="/api/v1/search", tags=["search-v3"])
logger = logging.getLogger(__name__)
SEARCH_READ = require_permission("search.read")
EXAMPLES = ["cat", "cat mama", "cat, est, 2015", "\"est 2015\"", "cat OR dog", "subject:cat", "text:\"mama\""]

def search_config(session, tenant):
    profiles = list(session.scalars(select(MetadataProfileModel).where(MetadataProfileModel.tenant_id == tenant, MetadataProfileModel.active.is_(True))))
    facets, aliases, boosts = set(), {}, {}
    for profile in profiles:
        config = profile.search_config_json or {}
        values = config.get("facet_paths", [])
        if isinstance(values, dict):
            values = list(values)
        facets.update(str(value) for value in values if isinstance(value, str))
        aliases.update({str(k): str(v) for k, v in (config.get("field_aliases") or {}).items()})
        boosts.update({str(k): float(v) for k, v in (config.get("boost_paths") or {}).items() if isinstance(v, (int, float))})
    return SearchQueryConfig(facet_names=frozenset(facets), path_aliases=aliases, boost_paths=boosts), sorted(facets)

def _search_generation(session, tenant: str, settings) -> str:
    """Resolve V3 readiness without ever selecting a legacy generation."""
    if not settings.SEARCH_V3_ENABLED or not settings.ELASTICSEARCH_URL:
        return "unavailable"
    row = session.scalar(
        select(SearchIndexRecordModel)
        .where(
            SearchIndexRecordModel.index_prefix == settings.ELASTICSEARCH_INDEX_PREFIX,
            SearchIndexRecordModel.lifecycle_state == "active",
        )
        .order_by(
            SearchIndexRecordModel.activated_at.desc().nullslast(),
            SearchIndexRecordModel.created_at.desc(),
        )
    )
    if row is None:
        return "verification_unknown"
    verification = row.verification_json or {}
    if verification.get("passed") is False:
        return "incompatible"
    required_fields = {"tenant_id", "source_id", "ancestor_ids", "visible_text", "search_suggest", "filename.normalized"}
    raw_mapping_fields = verification.get("mapping_fields")
    if not raw_mapping_fields:
        return "verification_unknown"
    if not required_fields.issubset(set(raw_mapping_fields)):
        return "incompatible"
    verification_checks = ("mapping_matches", "analyzer_matches", "projection_version_documents_match")
    if any(verification.get(name) is False for name in verification_checks):
        return "incompatible"
    if verification.get("passed") is not True or any(name not in verification for name in verification_checks):
        return "verification_unknown"
    return "ready"


def _require_v3(readiness: str, settings) -> None:
    if readiness == "ready":
        return
    if readiness == "verification_unknown":
        if settings.SEARCH_V3_REQUIRED:
            logger.warning("Search V3 governance verification is unknown; using compatibility mode")
        return
    message = (
        "Search V3 index verification is incompatible with this application version."
        if readiness == "incompatible"
        else "Search V3 is unavailable."
    )
    raise HTTPException(
        status_code=503,
        detail={"code": "search_v3_unavailable", "message": message, "retryable": True},
    )


@router.get("/capabilities", response_model=SearchCapabilities)
def capabilities(principal: CurrentPrincipal = Depends(SEARCH_READ)):
    tenant = principal.active_tenant_id
    settings = get_settings()
    with SessionLocal() as session:
        _, facets = search_config(session, tenant)
        readiness = _search_generation(session, tenant, settings)
        session.commit()
    search_available = readiness in {"ready", "verification_unknown"}
    return {
        "selected_version": "v3",
        "readiness": readiness,
        "search_available": search_available,
        "viewer_scoped": is_pure_viewer(principal),
        "failure_code": None if search_available else "search_v3_unavailable",
        "facet_names": facets,
        "examples": EXAMPLES,
    }


def _source_provider_filter(session, tenant: str, source_provider: str | None, *, external_source_id: str | None = None) -> dict | None:
    if not source_provider and not external_source_id:
        return None
    source_type = "google_drive" if source_provider == "google-drive" else "sharepoint" if source_provider == "sharepoint" else None
    source_where = [ExternalSourceModel.tenant_id == tenant]
    if source_type:
        source_where.append(ExternalSourceModel.source_type == source_type)
    if external_source_id:
        source_where.append(ExternalSourceModel.id == external_source_id)
    source_ids = [
        str(source_id)
        for source_id in session.scalars(
            select(ExternalSourceModel.id).where(
                *source_where,
            )
        )
    ]
    return {"terms": {"source_id": source_ids or ["__none__"]}}
def _viewer_scope_filter(
    session,
    principal: CurrentPrincipal,
) -> tuple[dict | None, tuple[tuple[str, tuple[str, ...]], ...] | None]:
    if not is_pure_viewer(principal) or not principal.membership_id:
        return ({"match_none": {}}, ()) if is_pure_viewer(principal) else (None, None)
    scopes = ViewerFolderScopeService(session).list_membership_scopes(
        tenant_id=principal.active_tenant_id,
        membership_id=principal.membership_id,
    )
    normalized = tuple(sorted(
        (str(source_id), tuple(sorted(str(folder_id) for folder_id in folder_ids if str(folder_id).strip())))
        for source_id, folder_ids in scopes.items() if folder_ids
    ))
    if not normalized:
        return {"match_none": {}}, normalized
    return {"bool": {"should": [
        {"bool": {"filter": [{"term": {"source_id": source_id}}, {"terms": {"ancestor_ids": list(folder_ids)}}]}}
        for source_id, folder_ids in normalized
    ], "minimum_should_match": 1}}, normalized


def _search_scope_filters(
    session,
    principal: CurrentPrincipal,
    *,
    source_provider: str | None,
    external_source_id: str | None,
) -> tuple[list[dict], tuple[tuple[str, tuple[str, ...]], ...] | None, bool]:
    filters: list[dict] = [{"term": {"tenant_id": principal.active_tenant_id}}]
    source_filter = _source_provider_filter(
        session,
        principal.active_tenant_id,
        source_provider,
        external_source_id=external_source_id,
    )
    if source_filter:
        filters.append(source_filter)
    viewer_filter, viewer_scope_key = _viewer_scope_filter(
        session, principal,
    )
    if viewer_filter:
        filters.append(viewer_filter)
    return filters, viewer_scope_key, viewer_filter is not None


def _live_suggestion_hits(session, tenant: str, hits: list[dict]) -> list[dict]:
    identities = {
        (str(hit.get("_source", {}).get("asset_id") or hit.get("_id") or ""),
         str(hit.get("_source", {}).get("source_id") or ""))
        for hit in hits
        if str(hit.get("_source", {}).get("asset_id") or hit.get("_id") or "")
        and str(hit.get("_source", {}).get("source_id") or "")
    }
    if not identities:
        return hits
    asset_ids = {asset_id for asset_id, _source_id in identities}
    rows = session.execute(
        select(AssetSourceLinkModel.asset_id, SourceAssetModel.external_source_id)
        .join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id)
        .where(
            AssetSourceLinkModel.tenant_id == tenant,
            AssetSourceLinkModel.asset_id.in_(asset_ids),
            SourceAssetModel.deleted_at.is_(None),
        )
    ).all()
    live = {(str(asset_id), str(source_id)) for asset_id, source_id in rows}
    return [
        hit for hit in hits
        if not hit.get("_source", {}).get("source_id")
        or (str(hit.get("_source", {}).get("asset_id") or hit.get("_id")), str(hit["_source"]["source_id"])) in live
    ]


def _source_timestamp(value) -> float:
    try:
        return value.timestamp()
    except (AttributeError, OSError, OverflowError, ValueError):
        return 0.0


def _source_pair_rank(source: SourceAssetModel, external: ExternalSourceModel) -> tuple:
    """Prefer the current/default live source when an asset has duplicate links."""
    metadata = external.source_metadata or {}
    return (
        bool(metadata.get("is_default")),
        _source_timestamp(external.updated_at),
        _source_timestamp(source.last_seen_at or source.updated_at),
        _source_timestamp(source.updated_at),
        str(source.id),
    )

def _search_thumbnail_url(*, provider: str, external_asset_id: str, external_source_id: str, kind: str) -> str | None:
    if provider != "google-drive" or kind not in {"image", "video"}:
        return None
    query = urlencode(
        {
            "provider": provider,
            "external_source_id": str(external_source_id),
        }
    )
    return f"/api/explorer/thumbnail/{quote(str(external_asset_id), safe='')}?{query}"


def _facet_aggregations(allowed_facets: list[str], include_facets: bool) -> dict[str, dict]:
    if not include_facets:
        return {}
    return {
        name: {"terms": {"field": f"facets.{name}", "size": 50}}
        for name in allowed_facets
    }



def _completion_value(value: object, query: str) -> str | None:
    text = " ".join(str(value or "").split())
    needle = " ".join(query.split()).casefold()
    if not text or not needle:
        return None
    index = text.casefold().find(needle)
    if index < 0:
        return None
    while index and text[index - 1].isalnum():
        index -= 1
    suggestion = text[index:index + 160].rstrip(" ,;:-")
    if not suggestion[: len(needle)].casefold().startswith(needle):
        return None
    return suggestion


def _completion_variants(value: object, query: str, *, include_exact: bool = False) -> list[tuple[str, str]]:
    suggestion = _completion_value(value, query)
    normalized_query = " ".join(query.split())
    if not suggestion or not normalized_query:
        return []
    query_words = len(normalized_query.split())
    words = suggestion.split()
    if len(words) <= query_words:
        return [(suggestion, suggestion[len(normalized_query):])]
    variants: list[tuple[str, str]] = []
    if include_exact:
        variants.append((suggestion, suggestion[len(normalized_query):]))
    for end in range(query_words + 1, min(len(words), query_words + 4) + 1):
        text = " ".join(words[:end]).rstrip(" ,;:-")
        if len(text) <= 80:
            variants.append((text, text[len(normalized_query):]))
    return variants


def _suggestion_values(document: dict, query: str) -> list[tuple[str, str, str]]:
    values: list[tuple[str, str, str]] = []
    exact_terms = [
        str(value).strip()
        for key in ("search_terms", "normalized_terms")
        for value in (document.get(key) or [])
        if isinstance(value, str)
    ]
    if any(value.casefold() == query.casefold() for value in exact_terms):
        values.append(("search_text", query, ""))

    visible_text = document.get("visible_text")
    if isinstance(visible_text, list):
        visible_text = " ".join(str(value) for value in visible_text if isinstance(value, str))
    for text, completion in _completion_variants(visible_text, query):
        values.append(("visible_text", text, completion))
    for text, completion in _completion_variants(document.get("filename"), query, include_exact=True):
        values.append(("filename", text, completion))
    if not any(kind in {"visible_text", "search_text"} for kind, _, _ in values):
        for text, completion in _completion_variants(document.get("search_suggest"), query):
            values.append(("search_text", text, completion))
    return values


@router.get("/suggestions", response_model=SearchSuggestionsResponse)
async def suggestions(
    q: str = Query(min_length=2, max_length=160),
    source_provider: str | None = Query(default=None, pattern="^(google-drive|sharepoint)$"),
    external_source_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=7, ge=1, le=10),
    principal: CurrentPrincipal = Depends(SEARCH_READ),
):
    tenant = principal.active_tenant_id
    settings = get_settings()
    if is_pure_viewer(principal) and not (external_source_id or '').strip():
        raise HTTPException(422, detail={'code': 'viewer_source_required', 'message': 'A source is required for scoped Viewer search.'})
    with SessionLocal() as session:
        readiness = _search_generation(session, tenant, settings)
        _require_v3(readiness, settings)
        generation = "v3"
        filters, viewer_scope_key, _viewer_restricted = _search_scope_filters(
            session, principal,
            source_provider=source_provider,
            external_source_id=external_source_id,
            )
        session.commit()
    value = q.strip()
    cache_key = (tenant, source_provider or "", external_source_id or "", generation, viewer_scope_key, value.casefold(), limit)
    cached = SEARCH_SUGGESTION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    query = {
        "_source": ["asset_id", "source_id", "filename", "visible_text", "search_suggest", "search_terms", "normalized_terms"],
        "size": min(limit * 2, 16),
        "terminate_after": 100,
        "timeout": f"{settings.SEARCH_SUGGESTIONS_QUERY_TIMEOUT_MS}ms",
        "track_total_hits": False,
        "query": {
            "bool": {
                "filter": filters,
                "should": [
                    {"match_phrase_prefix": {"visible_text": {"query": value, "boost": 12, "max_expansions": 20}}},
                    {"match_phrase_prefix": {"filename": {"query": value, "boost": 8, "max_expansions": 20}}},
                    {"multi_match": {"query": value, "type": "bool_prefix", "fields": ["search_suggest", "search_suggest._2gram", "search_suggest._3gram"], "boost": 4}},
                ],
                "minimum_should_match": 1,
            }
        },
    }
    try:
        index = await API_SEARCH_INDEX_POOL.get(
            ElasticsearchV3Config(
                settings.ELASTICSEARCH_URL,
                settings.ELASTICSEARCH_INDEX_PREFIX,
                request_timeout_seconds=settings.SEARCH_SUGGESTIONS_REQUEST_TIMEOUT_SECONDS,
                index_generation="v3",
            )
        )
        response = await index.search(query)
    except ElasticsearchV3RequestError as exc:
        raise HTTPException(503, detail={
            "code": "search_v3_unavailable",
            "message": "Search V3 is temporarily unavailable.",
            "retryable": True,
        }) from exc
    seen: set[str] = set()
    candidates = []
    raw_hits = response.get("hits", {}).get("hits", [])
    with SessionLocal() as session:
        live_hits = _live_suggestion_hits(session, tenant, raw_hits)
    for hit in live_hits:
        for kind, text, completion in _suggestion_values(hit.get("_source", {}), value):
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"text": text, "prefix": text[:len(text) - len(completion)], "completion": completion, "kind": kind})
    kind_rank = {"search_text": 0, "visible_text": 1, "filename": 2}
    result = sorted(candidates, key=lambda item: (len(item["text"]), kind_rank[item["kind"]], item["text"].casefold()))[:limit]
    payload = {"search_version": generation, "suggestions": result, "took_ms": response.get("took")}
    if not response.get("timed_out"):
        SEARCH_SUGGESTION_CACHE.put(
            cache_key,
            payload,
            ttl_seconds=settings.SEARCH_SUGGESTIONS_CACHE_TTL_SECONDS,
            max_entries=settings.SEARCH_SUGGESTIONS_CACHE_MAX_ENTRIES,
        )
    return payload

def _hydrate_search_hits(session, tenant: str, hits: list[dict], *, viewer_restricted: bool, limit: int) -> list[dict]:
    asset_ids = [str(hit.get("_source", {}).get("asset_id") or hit.get("_id")) for hit in hits]
    document_source_ids = {str(hit.get("_source", {}).get("source_id") or "").strip() for hit in hits if str(hit.get("_source", {}).get("source_id") or "").strip()}
    if not asset_ids:
        return []
    rows_query = (select(AssetSourceLinkModel.asset_id, SourceAssetModel, ExternalSourceModel)
        .join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id)
        .join(ExternalSourceModel, ExternalSourceModel.id == SourceAssetModel.external_source_id)
        .where(AssetSourceLinkModel.tenant_id == tenant, AssetSourceLinkModel.asset_id.in_(asset_ids), SourceAssetModel.deleted_at.is_(None)))
    if viewer_restricted:
        rows_query = rows_query.where(ExternalSourceModel.id.in_(document_source_ids or ["__none__"]))
    rows = session.execute(rows_query).all()
    sources: dict[tuple[str, str], tuple[SourceAssetModel, ExternalSourceModel]] = {}
    for aid, source, external in sorted(rows, key=lambda row: _source_pair_rank(row[1], row[2]), reverse=True):
        sources.setdefault((str(aid), str(external.id)), (source, external))
    items = []
    for hit in hits:
        doc = hit.get("_source", {})
        aid = str(doc.get("asset_id") or hit.get("_id"))
        document_source_id = str(doc.get("source_id") or "").strip()
        pair = sources.get((aid, document_source_id))
        if pair is None and not viewer_restricted:
            pair = next((value for (candidate, _), value in sources.items() if candidate == aid), None)
        if not pair:
            continue
        source, external = pair
        provider = "sharepoint" if external.source_type == "sharepoint" else "google-drive"
        mime = infer_media_type(source.filename or doc.get("filename"), source.mime_type)
        kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "pdf" if mime == "application/pdf" else "document"
        items.append({"provider": provider, "id": source.external_asset_id, "internal_asset_id": aid, "external_source_id": source.external_source_id, "name": source.filename or doc.get("filename") or "Untitled", "kind": kind, "mime_type": mime, "modified_at": source.source_modified_at.isoformat() if source.source_modified_at else None, "thumbnail_url": _search_thumbnail_url(provider=provider, external_asset_id=source.external_asset_id, external_source_id=source.external_source_id, kind=kind), "web_url": resolve_source_web_url(provider=provider, external_asset_id=source.external_asset_id, source_metadata=source.source_metadata), "folder_path": doc.get("folder_path"), "score": hit.get("_score")})
        if len(items) >= limit:
            break
    return items

@router.post("", response_model=SearchV3Response)
async def search(body: SearchV3Request, principal: CurrentPrincipal = Depends(SEARCH_READ)):
    tenant = principal.active_tenant_id
    settings = get_settings()
    with SessionLocal() as session:
        readiness = _search_generation(session, tenant, settings)
        if is_pure_viewer(principal) and not (body.external_source_id or "").strip():
            raise HTTPException(status_code=422, detail={"code": "viewer_source_required", "message": "A search source is required."})
        _require_v3(readiness, settings)
        generation = "v3"
        config, allowed_facets = search_config(session, tenant)
        unknown = set(body.facets) - set(allowed_facets)
        if unknown:
            raise HTTPException(422, f"Unsupported search facets: {sorted(unknown)}")
        parsed = SearchQueryParser().parse(body.query)
        if body.cursor and body.offset:
            raise HTTPException(422, "Offset and cursor pagination cannot be combined")
        try:
            search_after = decode_search_cursor(body.cursor) if body.cursor else None
        except ValueError as exc:
            raise HTTPException(422, "Invalid search cursor") from exc
        query = ElasticsearchQueryBuilder().build(
            parsed,
            tenant_id=tenant,
            config=config,
            size=body.limit,
            offset=body.offset,
            search_after=search_after,
        )
        filters, _viewer_scope_key, viewer_restricted = _search_scope_filters(
            session, principal,
            source_provider=body.source_provider,
            external_source_id=body.external_source_id,
            )
        query["query"]["bool"]["filter"] = filters
        for name, values in sorted(body.facets.items()):
            if values:
                filters.append({"terms": {f"facets.{name}": values}})
        aggregations = _facet_aggregations(allowed_facets, body.include_facets)
        if aggregations:
            query["aggs"] = aggregations
        debug = body.debug and (principal.platform_admin or "search.rebuild" in principal.effective_permissions)
        candidate_limit = min(max(body.limit + 30, body.limit), 180)
        chunk_size = min(body.limit + 30, 90)
        query["size"] = chunk_size
        try:
            index = await API_SEARCH_INDEX_POOL.get(
                ElasticsearchV3Config(
                    settings.ELASTICSEARCH_URL,
                    settings.ELASTICSEARCH_INDEX_PREFIX,
                    index_generation="v3",
                )
            )
            response = await index.search(query)
        except ElasticsearchV3RequestError as exc:
            raise HTTPException(503, detail={
                "code": "search_v3_unavailable",
                "message": "Search V3 is temporarily unavailable.",
                "retryable": True,
            }) from exc
        first_response = response
        hits = list(response.get("hits", {}).get("hits", []))
        consumed = len(hits)
        last_sort = hits[-1].get("sort") if hits else None
        items = _hydrate_search_hits(session, tenant, hits, viewer_restricted=viewer_restricted, limit=body.limit)
        can_continue = bool(len(hits) == chunk_size and isinstance(last_sort, list))
        while len(items) < body.limit and can_continue and consumed < candidate_limit:
            next_size = min(chunk_size, candidate_limit - consumed)
            if next_size < 1:
                break
            query["search_after"] = last_sort
            query.pop("from", None)
            query["size"] = next_size
            try:
                next_response = await index.search(query)
            except ElasticsearchV3RequestError as exc:
                raise HTTPException(503, detail={
                    "code": "search_v3_unavailable",
                    "message": "Search V3 is temporarily unavailable.",
                    "retryable": True,
                }) from exc
            batch = list(next_response.get("hits", {}).get("hits", []))
            if not batch:
                can_continue = False
                break
            next_sort = batch[-1].get("sort")
            if next_sort == last_sort or not isinstance(next_sort, list):
                can_continue = False
                break
            hits.extend(batch)
            consumed += len(batch)
            last_sort = next_sort
            items = _hydrate_search_hits(session, tenant, hits, viewer_restricted=viewer_restricted, limit=body.limit)
            can_continue = len(batch) == next_size
        response = first_response
        total_value = response.get("hits", {}).get("total", 0)
        total = int(total_value.get("value", 0) if isinstance(total_value, dict) else total_value)
        facet_output = (
            {name: [{"value": bucket.get("key"), "count": bucket.get("doc_count", 0)} for bucket in response.get("aggregations", {}).get(name, {}).get("buckets", [])] for name in allowed_facets}
            if body.include_facets
            else {}
        )
        parsed_doc = {"mode": parsed.mode.value, "clauses": [{"kind": clause.kind.value, "field": clause.field, "value": clause.value} for clause in parsed.clauses]} if debug else None
        next_cursor = None
        if can_continue and last_sort:
            try:
                next_cursor = encode_search_cursor(last_sort)
            except ValueError:
                next_cursor = None
        primary_result = {"search_version": generation, "items": items, "total": total, "facets": facet_output, "parsed_query": parsed_doc, "took_ms": response.get("took"), "next_cursor": next_cursor, "has_more": next_cursor is not None}
        return primary_result
