from __future__ import annotations
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from urllib.parse import quote
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV2Config, ElasticsearchV2Index, ElasticsearchV2RequestError
from app.modules.explorer.router import _access_token, _account_id
from app.modules.explorer.schema import SearchRequest
from app.modules.explorer.service import ExplorerService
from app.providers.source_factory import create_source_provider
from app.modules.search.shadow_runtime import SHADOW_SEARCH
from app.modules.ai_metadata.model import MetadataProfileModel
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.processing_policy.repository import ProcessingPolicyRepository
from app.modules.processing_policy.service import ProcessingPolicyService
from app.modules.search.query_builder import ElasticsearchQueryBuilder, SearchQueryConfig
from app.modules.search.query_parser import SearchQueryParser
from app.modules.search.schema import SearchCapabilities, SearchV2Request, SearchV2Response

router = APIRouter(prefix="/api/v1/search", tags=["search-v2"])
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

def enabled(session, tenant):
    settings = get_settings()
    effective = ProcessingPolicyService(ProcessingPolicyRepository(session), settings).effective(tenant)
    return bool(effective.effective.get("search_v2_enabled") and settings.SEARCH_QUERY_PARSER_V2_ENABLED and settings.ELASTICSEARCH_URL)

@router.get("/capabilities", response_model=SearchCapabilities)
def capabilities(principal: CurrentPrincipal = Depends(SEARCH_READ)):
    tenant = principal.active_tenant_id
    with SessionLocal() as session:
        config, facets = search_config(session, tenant)
        available = enabled(session, tenant)
        session.commit()
    return {"selected_version": "v2" if available else "v1", "v2_available": available, "parser_available": get_settings().SEARCH_QUERY_PARSER_V2_ENABLED, "debug_allowed": principal.platform_admin or "search.rebuild" in principal.effective_permissions, "facet_names": facets, "examples": EXAMPLES}

@router.post("", response_model=SearchV2Response)
async def search(body: SearchV2Request, request: Request, principal: CurrentPrincipal = Depends(SEARCH_READ)):
    tenant = principal.active_tenant_id
    settings = get_settings()
    with SessionLocal() as session:
        if not enabled(session, tenant):
            raise HTTPException(409, "Search v2 is not enabled for this tenant")
        config, allowed_facets = search_config(session, tenant)
        unknown = set(body.facets) - set(allowed_facets)
        if unknown:
            raise HTTPException(422, f"Unsupported search facets: {sorted(unknown)}")
        parsed = SearchQueryParser().parse(body.query)
        query = ElasticsearchQueryBuilder().build(parsed, tenant_id=tenant, config=config, size=body.limit, offset=body.offset)
        filters = query["query"]["bool"]["filter"]
        for name, values in sorted(body.facets.items()):
            if values:
                filters.append({"terms": {f"facets.{name}": values}})
        if body.source_provider:
            source_type = "google_drive" if body.source_provider == "google-drive" else "sharepoint"
            source_asset_ids = select(AssetSourceLinkModel.asset_id).join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id).join(ExternalSourceModel, ExternalSourceModel.id == SourceAssetModel.external_source_id).where(AssetSourceLinkModel.tenant_id == tenant, ExternalSourceModel.source_type == source_type)
            asset_ids = list(session.scalars(source_asset_ids))
            filters.append({"terms": {"asset_id": asset_ids or ["__none__"]}})
        query["aggs"] = {name: {"terms": {"field": f"facets.{name}", "size": 50}} for name in allowed_facets}
        debug = body.debug and (principal.platform_admin or "search.rebuild" in principal.effective_permissions)
        primary_started = time.perf_counter()
        try:
            async with ElasticsearchV2Index(ElasticsearchV2Config(settings.ELASTICSEARCH_URL, settings.ELASTICSEARCH_INDEX_PREFIX)) as index:
                response = await index.search(query)
        except ElasticsearchV2RequestError as exc:
            raise HTTPException(503, "Search service is temporarily unavailable") from exc
        hits = response.get("hits", {}).get("hits", [])
        asset_ids = [str(hit.get("_source", {}).get("asset_id") or hit.get("_id")) for hit in hits]
        rows = session.execute(select(AssetSourceLinkModel.asset_id, SourceAssetModel, ExternalSourceModel).join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id).join(ExternalSourceModel, ExternalSourceModel.id == SourceAssetModel.external_source_id).where(AssetSourceLinkModel.tenant_id == tenant, AssetSourceLinkModel.asset_id.in_(asset_ids)).order_by(AssetSourceLinkModel.created_at)).all()
        sources = {}
        for aid, source, external in rows:
            sources.setdefault(aid, (source, external))
        items = []
        for hit in hits:
            doc = hit.get("_source", {})
            aid = str(doc.get("asset_id") or hit.get("_id"))
            pair = sources.get(aid)
            if not pair:
                continue
            source, external = pair
            provider = "sharepoint" if external.source_type == "sharepoint" else "google-drive"
            mime = source.mime_type or "application/octet-stream"
            kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "pdf" if mime == "application/pdf" else "document"
            items.append({"provider": provider, "id": source.external_asset_id, "internal_asset_id": aid, "external_source_id": source.external_source_id, "name": source.filename or doc.get("filename") or "Untitled", "kind": kind, "mime_type": mime, "modified_at": source.source_modified_at.isoformat() if source.source_modified_at else None, "thumbnail_url": f"/api/explorer/media/{quote(source.external_asset_id, safe='')}?provider={provider}" if kind in {"image", "video"} else None, "folder_path": doc.get("folder_path"), "score": hit.get("_score")})
        total_value = response.get("hits", {}).get("total", 0)
        total = int(total_value.get("value", 0) if isinstance(total_value, dict) else total_value)
        facet_output = {name: [{"value": bucket.get("key"), "count": bucket.get("doc_count", 0)} for bucket in response.get("aggregations", {}).get(name, {}).get("buckets", [])] for name in allowed_facets}
        parsed_doc = {"mode": parsed.mode.value, "clauses": [{"kind": clause.kind.value, "field": clause.field, "value": clause.value} for clause in parsed.clauses]} if debug else None
        primary_result = {"search_version": "v2", "items": items, "total": total, "facets": facet_output, "parsed_query": parsed_doc, "took_ms": response.get("took")}
        provider = body.source_provider or "google-drive"

        async def legacy_shadow():
            token = await _access_token(request, provider)
            result = await ExplorerService(create_source_provider).search_subtree(
                SearchRequest(
                    provider=provider, query=body.query, root_id="root",
                    limit=min(body.limit, 200),
                ),
                token, _account_id(request, provider),
            )
            document = result.model_dump(mode="json")
            document["total"] = len(document["items"])
            return document

        # Aggregate v2 results are not comparable to one provider-specific legacy tree.
        if body.source_provider:
            await SHADOW_SEARCH.observe(
                tenant_id=tenant, query=body.query, primary_result=primary_result,
                primary_ms=int((time.perf_counter() - primary_started) * 1000),
                shadow=legacy_shadow, primary_version="v2", shadow_version="v1",
                surface="search_v2",
            )
        return primary_result
