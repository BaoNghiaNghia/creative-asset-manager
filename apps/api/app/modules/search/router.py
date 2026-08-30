from __future__ import annotations
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from urllib.parse import quote, urlencode
from sqlalchemy import case, func, or_, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3RequestError
from app.modules.explorer.media_types import infer_media_type
from app.modules.explorer.router import _provider_error, _source_context, _viewer_folder_scope_allowed
from app.providers.source_factory import create_source_provider
from app.modules.search.runtime import (
    API_SEARCH_INDEX_POOL,
    SEARCH_CONFIG_CACHE,
    SEARCH_SUGGESTION_CACHE,
)
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
    search_request_fingerprint,
)
from app.modules.search.query_parser import SearchQueryParser
from app.modules.search.schema import SearchCapabilities, SearchSuggestionsResponse, SearchV3Request, SearchV3Response
from app.modules.search.governance_model import SearchIndexRecordModel

router = APIRouter(prefix="/api/v1/search", tags=["search-v3"])
logger = logging.getLogger(__name__)
SEARCH_READ = require_permission("search.read")
EXAMPLES = ["cat", "cat mama", "cat, est, 2015", "\"est 2015\"", "cat OR dog", "subject:cat", "text:\"mama\""]



def enabled(*_args, **_kwargs) -> bool:
    """Compatibility hook; Search V3 is the only runtime generation."""
    return True
def search_config(session, tenant):
    count, revision = session.execute(
        select(
            func.count(MetadataProfileModel.id),
            func.max(MetadataProfileModel.updated_at),
        ).where(
            MetadataProfileModel.tenant_id == tenant,
            MetadataProfileModel.active.is_(True),
        )
    ).one()
    cache_key = (
        id(session.get_bind()),
        tenant,
        int(count or 0),
        str(revision or ""),
    )
    cached = SEARCH_CONFIG_CACHE.get(cache_key)
    if cached is not None:
        config, facets = cached
        return config, list(facets)
    profiles = list(session.scalars(
        select(MetadataProfileModel).where(
            MetadataProfileModel.tenant_id == tenant,
            MetadataProfileModel.active.is_(True),
        )
    ))
    facets, aliases, boosts = set(), {}, {}
    for profile in profiles:
        profile_config = profile.search_config_json or {}
        values = profile_config.get("facet_paths", [])
        if isinstance(values, dict):
            values = list(values)
        facets.update(str(value) for value in values if isinstance(value, str))
        aliases.update({
            str(key): str(value)
            for key, value in (profile_config.get("field_aliases") or {}).items()
        })
        boosts.update({
            str(key): float(value)
            for key, value in (profile_config.get("boost_paths") or {}).items()
            if isinstance(value, (int, float))
        })
    result = (
        SearchQueryConfig(
            facet_names=frozenset(facets),
            path_aliases=aliases,
            boost_paths=boosts,
        ),
        tuple(sorted(facets)),
    )
    SEARCH_CONFIG_CACHE.put(cache_key, result)
    return result[0], list(result[1])

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


def _source_provider_filter(session, tenant: str, source_provider: str | None, *, external_source_id: str | None = None, generation: str | None = None) -> dict | None:
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
    *,
    generation: str | None = None,
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

def _folder_parent_id(source: SourceAssetModel) -> str | None:
    metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
    parents = metadata.get("parents")
    if not isinstance(parents, list):
        return None
    return next((str(value).strip() for value in parents if str(value).strip()), None)

def _folder_breadcrumb(session, tenant: str, source: SourceAssetModel) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    names: list[str] = []
    seen = {source.external_asset_id}
    parent_id = _folder_parent_id(source)
    for _depth in range(100):
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        parent = session.scalar(select(SourceAssetModel).where(
            SourceAssetModel.tenant_id == tenant,
            SourceAssetModel.external_source_id == source.external_source_id,
            SourceAssetModel.external_asset_id == parent_id,
            SourceAssetModel.deleted_at.is_(None),
        ))
        if parent is None:
            break
        ids.append(parent.external_asset_id)
        names.append(parent.filename or "Folder")
        parent_id = _folder_parent_id(parent)
    ids.reverse()
    names.reverse()
    return ids, names

def _normalize_asin_folder_query(value: str) -> str | None:
    normalized = value.strip().upper()
    return normalized if re.fullmatch(r"[A-Z0-9]{10}", normalized) else None


def _search_folder_items(session, principal: CurrentPrincipal, *, value: str, source_provider: str | None, external_source_id: str | None, limit: int) -> list[dict]:
    tenant = principal.active_tenant_id
    asin = _normalize_asin_folder_query(value)
    if asin is None:
        return []
    normalized = asin.casefold()
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conditions = [
        SourceAssetModel.tenant_id == tenant,
        SourceAssetModel.deleted_at.is_(None),
        func.lower(func.coalesce(SourceAssetModel.filename, "")).like(f"%{escaped}%", escape="\\"),
        or_(
            SourceAssetModel.mime_type == "application/vnd.google-apps.folder",
            func.lower(func.coalesce(SourceAssetModel.mime_type, "")) == "folder",
            SourceAssetModel.source_metadata["is_folder"].as_boolean().is_(True),
        ),
    ]
    source_type = "google_drive" if source_provider == "google-drive" else "sharepoint" if source_provider == "sharepoint" else None
    if source_type:
        conditions.append(ExternalSourceModel.source_type == source_type)
    if external_source_id:
        conditions.append(ExternalSourceModel.id == external_source_id)
    rows = session.execute(
        select(SourceAssetModel, ExternalSourceModel)
        .join(ExternalSourceModel, ExternalSourceModel.id == SourceAssetModel.external_source_id)
        .where(*conditions)
        .order_by(
            case((func.lower(SourceAssetModel.filename) == normalized, 0), else_=1),
            func.lower(SourceAssetModel.filename),
            SourceAssetModel.external_asset_id,
        )
        .limit(limit)
    ).all()
    viewer_scopes = None
    if is_pure_viewer(principal):
        if not principal.membership_id:
            return []
        viewer_scopes = ViewerFolderScopeService(session).list_membership_scopes(
            tenant_id=tenant,
            membership_id=principal.membership_id,
        )
    items: list[dict] = []
    for source, external in rows:
        ancestor_ids, ancestor_names = _folder_breadcrumb(session, tenant, source)
        if viewer_scopes is not None:
            allowed_roots = set(viewer_scopes.get(source.external_source_id, ()))
            if not allowed_roots.intersection([source.external_asset_id, *ancestor_ids]):
                continue
        provider = "sharepoint" if external.source_type == "sharepoint" else "google-drive"
        items.append({
            "provider": provider,
            "id": source.external_asset_id,
            "external_source_id": source.external_source_id,
            "name": source.filename or "Untitled folder",
            "kind": "folder",
            "mime_type": source.mime_type or "application/vnd.google-apps.folder",
            "modified_at": source.source_modified_at.isoformat() if source.source_modified_at else None,
            "web_url": resolve_source_web_url(provider=provider, external_asset_id=source.external_asset_id, source_metadata=source.source_metadata),
            "ancestor_ids": ancestor_ids,
            "ancestor_names": ancestor_names,
            "has_children": True,
        })
    return items

async def _provider_folder_breadcrumb(client, folder, cache: dict[str, object]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    names: list[str] = []
    parent_id = folder.parent_id
    seen = {folder.id}
    for _depth in range(64):
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        parent = cache.get(parent_id)
        if parent is None:
            parent = await client.get_node(parent_id)
            cache[parent_id] = parent
        ids.append(parent.id)
        names.append(parent.name or "Folder")
        parent_id = parent.parent_id
    ids.reverse()
    names.reverse()
    return ids, names

@router.get("/folders")
async def search_folders(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    source_provider: str | None = Query(default=None, pattern="^(google-drive|sharepoint)$"),
    external_source_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=20, ge=1, le=50),
    principal: CurrentPrincipal = Depends(SEARCH_READ),
):
    asin = _normalize_asin_folder_query(q)
    if asin is None:
        return {"items": [], "total": 0}
    if is_pure_viewer(principal) and not (external_source_id or "").strip():
        raise HTTPException(status_code=422, detail={"code": "viewer_source_required", "message": "A search source is required."})
    provider = source_provider or "google-drive"
    with SessionLocal() as session:
        items = _search_folder_items(session, principal, value=asin, source_provider=source_provider, external_source_id=external_source_id, limit=limit)
        if items or provider != "google-drive":
            return {"items": items, "total": len(items)}
        try:
            token, _account_id, tenant_id, resolved_source_id = await _source_context(
                request, provider, session, principal, external_source_id
            )
            if not token or not resolved_source_id:
                raise HTTPException(status_code=401, detail="Connect Google Drive to search folders.")
            scope_service = ViewerFolderScopeService(session)
            access = scope_service.access(
                tenant_id=tenant_id,
                membership_id=principal.membership_id,
                roles=principal.effective_roles,
                external_source_id=resolved_source_id,
            )
            live_items: list[dict] = []
            parent_cache: dict[str, object] = {}
            async with create_source_provider(provider, token) as client:
                folders = await client.search_folders(asin, limit=limit)
                for folder in folders:
                    if access.restricted and not await _viewer_folder_scope_allowed(
                        scope_service,
                        tenant_id=tenant_id,
                        access=access,
                        provider=provider,
                        token=token,
                        item_id=folder.id,
                    ):
                        continue
                    ancestor_ids, ancestor_names = await _provider_folder_breadcrumb(client, folder, parent_cache)
                    live_items.append({
                        "provider": provider,
                        "id": folder.id,
                        "external_source_id": resolved_source_id,
                        "name": folder.name or "Untitled folder",
                        "kind": "folder",
                        "mime_type": folder.mime_type or "application/vnd.google-apps.folder",
                        "modified_at": folder.modified_at.isoformat() if folder.modified_at else None,
                        "web_url": folder.web_url,
                        "ancestor_ids": ancestor_ids,
                        "ancestor_names": ancestor_names,
                        "has_children": True,
                    })
            return {"items": live_items, "total": len(live_items)}
        except HTTPException:
            raise
        except Exception as exc:
            raise _provider_error(exc, "Unable to search Google Drive folders") from exc

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


def _design_type_filter(values: list[str]) -> dict | None:
    if not values:
        return None
    return {
        "nested": {
            "path": "path_values",
            "query": {"terms": {"path_values.value": values}},
        }
    }


def _facet_filter(name: str, values: list[str]) -> dict | None:
    normalized = list(dict.fromkeys(value for value in values if value))
    return {"terms": {f"facets.{name}": normalized}} if normalized else None


def _facet_aggregations(
    allowed_facets: list[str],
    include_facets: bool,
    *,
    selected_facets: dict[str, list[str]] | None = None,
    persistent_filters: list[dict] | None = None,
) -> dict[str, dict]:
    if not include_facets:
        return {}
    selected_facets = selected_facets or {}
    persistent_filters = list(persistent_filters or ())
    aggregations: dict[str, dict] = {}
    for name in allowed_facets:
        other_filters = list(persistent_filters)
        for other_name, values in sorted(selected_facets.items()):
            if other_name == name:
                continue
            selected_filter = _facet_filter(other_name, values)
            if selected_filter:
                other_filters.append(selected_filter)
        selected_counts = {
            f"selected_{index}": {"term": {f"facets.{name}": value}}
            for index, value in enumerate(selected_facets.get(name, ()))
        }
        nested_aggs: dict[str, dict] = {
            "values": {"terms": {"field": f"facets.{name}", "size": 50}},
        }
        if selected_counts:
            nested_aggs["selected"] = {
                "filters": {"filters": selected_counts},
            }
        aggregations[name] = {
            "filter": (
                {"bool": {"filter": other_filters}}
                if other_filters
                else {"match_all": {}}
            ),
            "aggs": nested_aggs,
        }
    return aggregations


def _facet_response(
    response: dict,
    allowed_facets: list[str],
    selected_facets: dict[str, list[str]],
) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = {}
    response_aggs = response.get("aggregations", {})
    for name in allowed_facets:
        aggregation = response_aggs.get(name, {})
        buckets = aggregation.get("values", {}).get("buckets", [])
        values = [
            {"value": bucket.get("key"), "count": bucket.get("doc_count", 0)}
            for bucket in buckets
        ]
        represented = {str(item["value"]) for item in values}
        selected_buckets = aggregation.get("selected", {}).get("buckets", {})
        for index, selected in enumerate(selected_facets.get(name, ())):
            if selected in represented:
                continue
            selected_bucket = selected_buckets.get(f"selected_{index}", {})
            values.append({
                "value": selected,
                "count": int(selected_bucket.get("doc_count", 0)),
                "selected": True,
            })
        output[name] = values
    return output


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
async def search(
    body: SearchV3Request,
    principal: CurrentPrincipal = Depends(SEARCH_READ),
):
    tenant = principal.active_tenant_id
    settings = get_settings()
    with SessionLocal() as session:
        readiness = _search_generation(session, tenant, settings)
        if is_pure_viewer(principal) and not (body.external_source_id or "").strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "viewer_source_required",
                    "message": "A search source is required.",
                },
            )
        _require_v3(readiness, settings)
        generation = "v3"
        config, allowed_facets = search_config(session, tenant)
        unknown = set(body.facets) - set(allowed_facets)
        if unknown:
            raise HTTPException(
                422,
                f"Unsupported search facets: {sorted(unknown)}",
            )
        parsed = SearchQueryParser().parse(body.query or "")
        if body.cursor and body.offset:
            raise HTTPException(
                422,
                "Offset and cursor pagination cannot be combined",
            )
        filters, viewer_scope_key, viewer_restricted = _search_scope_filters(
            session,
            principal,
            source_provider=body.source_provider,
            external_source_id=body.external_source_id,
        )
        parsed_semantics = {
            "mode": parsed.mode.value,
            "clauses": [
                {
                    "kind": clause.kind.value,
                    "field": clause.field,
                    "value": clause.value,
                }
                for clause in parsed.clauses
            ],
        }
        fingerprint = search_request_fingerprint({
            "generation": generation,
            "tenant_id": tenant,
            "query": parsed_semantics,
            "facets": {
                name: sorted(values)
                for name, values in sorted(body.facets.items())
            },
            "design_types": sorted(body.design_types),
            "source_provider": body.source_provider,
            "external_source_id": body.external_source_id,
            "viewer_scope": viewer_scope_key,
            "sort": body.sort,
            "search_config": {
                "facet_names": sorted(config.facet_names),
                "path_aliases": dict(sorted(config.path_aliases.items())),
                "boost_paths": dict(sorted(config.boost_paths.items())),
                "soft_and": config.soft_and_minimum_should_match,
            },
        })
        try:
            cursor_state = (
                decode_search_cursor(
                    body.cursor,
                    expected_fingerprint=fingerprint,
                )
                if body.cursor
                else None
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        query = ElasticsearchQueryBuilder().build(
            parsed,
            tenant_id=tenant,
            config=config,
            size=body.limit,
            offset=body.offset,
            search_after=(
                cursor_state.sort_values if cursor_state is not None else None
            ),
            sort_mode=body.sort,
        )
        query["query"]["bool"]["filter"] = filters
        persistent_facet_filters: list[dict] = []
        design_type_filter = _design_type_filter(body.design_types)
        if design_type_filter:
            persistent_facet_filters.append(design_type_filter)
        selected_facet_filters = [
            selected_filter
            for name, values in sorted(body.facets.items())
            if (selected_filter := _facet_filter(name, values)) is not None
        ]
        post_filters = [*persistent_facet_filters, *selected_facet_filters]
        if post_filters:
            query["post_filter"] = {"bool": {"filter": post_filters}}
        aggregations = _facet_aggregations(
            allowed_facets,
            body.include_facets,
            selected_facets=body.facets,
            persistent_filters=persistent_facet_filters,
        )
        if aggregations:
            query["aggs"] = aggregations

        debug = body.debug and (
            principal.platform_admin
            or "search.rebuild" in principal.effective_permissions
        )
        candidate_limit = min(max(body.limit + 30, body.limit), 180)
        chunk_size = min(body.limit + 30, 90)
        query["size"] = chunk_size
        pit_id = cursor_state.pit_id if cursor_state is not None else None
        opened_here = False
        try:
            index = await API_SEARCH_INDEX_POOL.get(
                ElasticsearchV3Config(
                    settings.ELASTICSEARCH_URL,
                    settings.ELASTICSEARCH_INDEX_PREFIX,
                    index_generation="v3",
                )
            )
            if pit_id is None:
                pit_id = await index.open_point_in_time(keep_alive="2m")
                opened_here = True
            response = await index.search_with_pit(
                query,
                pit_id=pit_id,
                keep_alive="2m",
            )
            pit_id = str(response.get("pit_id") or pit_id)
        except ElasticsearchV3RequestError as exc:
            if opened_here and pit_id:
                try:
                    await index.close_point_in_time(pit_id)
                except Exception:
                    logger.warning("search_pit_cleanup_failed")
            raise HTTPException(
                503,
                detail={
                    "code": "search_v3_unavailable",
                    "message": "Search V3 is temporarily unavailable.",
                    "retryable": True,
                },
            ) from exc

        first_response = response
        query.pop("aggs", None)
        query["track_total_hits"] = False
        hits = list(response.get("hits", {}).get("hits", []))
        consumed = len(hits)
        last_sort = hits[-1].get("sort") if hits else None
        items = _hydrate_search_hits(
            session,
            tenant,
            hits,
            viewer_restricted=viewer_restricted,
            limit=body.limit,
        )
        can_continue = bool(
            len(hits) == chunk_size and isinstance(last_sort, list)
        )
        while (
            len(items) < body.limit
            and can_continue
            and consumed < candidate_limit
        ):
            next_size = min(chunk_size, candidate_limit - consumed)
            if next_size < 1:
                break
            query["search_after"] = last_sort
            query.pop("from", None)
            query["size"] = next_size
            try:
                next_response = await index.search_with_pit(
                    query,
                    pit_id=pit_id,
                    keep_alive="2m",
                )
                pit_id = str(next_response.get("pit_id") or pit_id)
            except ElasticsearchV3RequestError as exc:
                raise HTTPException(
                    503,
                    detail={
                        "code": "search_v3_unavailable",
                        "message": "Search V3 is temporarily unavailable.",
                        "retryable": True,
                    },
                ) from exc
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
            items = _hydrate_search_hits(
                session,
                tenant,
                hits,
                viewer_restricted=viewer_restricted,
                limit=body.limit,
            )
            can_continue = len(batch) == next_size

        response = first_response
        total_value = response.get("hits", {}).get("total", 0)
        if isinstance(total_value, dict):
            total = int(total_value.get("value", 0))
            total_relation = (
                "gte" if total_value.get("relation") == "gte" else "eq"
            )
        else:
            total = int(total_value)
            total_relation = "eq"
        facet_output = (
            _facet_response(response, allowed_facets, body.facets)
            if body.include_facets
            else {}
        )
        parsed_doc = parsed_semantics if debug else None
        next_cursor = None
        if can_continue and last_sort:
            try:
                next_cursor = encode_search_cursor(
                    last_sort,
                    fingerprint=fingerprint,
                    pit_id=pit_id,
                )
            except ValueError:
                next_cursor = None
        if next_cursor is None and pit_id:
            try:
                await index.close_point_in_time(pit_id)
            except ElasticsearchV3RequestError:
                logger.warning("search_pit_cleanup_failed")
        return {
            "search_version": generation,
            "items": items,
            "total": total,
            "total_relation": total_relation,
            "facets": facet_output,
            "parsed_query": parsed_doc,
            "took_ms": response.get("took"),
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        }
