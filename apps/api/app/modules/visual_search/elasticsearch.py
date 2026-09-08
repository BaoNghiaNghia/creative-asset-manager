from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config, ElasticsearchV3Index, ElasticsearchV3RequestError
from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding

_MAX_RESULTS = 100
_MAX_CANDIDATES = 1_000


class VisualSearchIndexError(ValueError):
    """Raised before a malformed visual-index request reaches Elasticsearch."""


@dataclass(frozen=True, slots=True)
class VisualSearchScope:
    """Internal tenant-bound scope; an unscoped KNN request is never valid."""

    tenant_id: str
    access_filters: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.tenant_id.strip():
            raise VisualSearchIndexError("visual search requires a tenant scope")
        if any(not isinstance(item, Mapping) for item in self.access_filters):
            raise VisualSearchIndexError("access filters must be Elasticsearch objects")

    def filters(self) -> list[dict[str, Any]]:
        return [
            {"term": {"tenant_id": self.tenant_id}},
            {"term": {"is_deleted": False}},
            {"term": {"is_hidden": False}},
            *(copy.deepcopy(dict(item)) for item in self.access_filters),
        ]


@dataclass(frozen=True, slots=True)
class VisualMetadataFilters:
    """Allowlisted asset metadata filters for the later public endpoint."""

    media_kind: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    source_provider: str | None = None
    source_id: str | None = None
    design_type: str | None = None

    def filters(self) -> list[dict[str, Any]]:
        values = {
            "media_kind": self.media_kind, "mime_type": self.mime_type,
            "extension": self.extension, "source_provider": self.source_provider,
            "source_id": self.source_id, "design_type": self.design_type,
        }
        return [
            {"term": {field: value.strip()}}
            for field, value in values.items()
            if isinstance(value, str) and value.strip()
        ]


@dataclass(frozen=True, slots=True)
class VisualIndexDocument:
    """Versioned vector projection for one tenant asset revision."""

    tenant_id: str
    asset_id: str
    content_sha256: str
    embedding: VisualEmbedding
    source_id: str | None = None
    source_provider: str | None = None
    media_kind: str = "image"
    mime_type: str | None = None
    extension: str | None = None
    design_type: tuple[str, ...] = ()
    ancestor_ids: tuple[str, ...] = ()
    is_deleted: bool = False
    is_hidden: bool = False

    def __post_init__(self) -> None:
        for value, name in ((self.tenant_id, "tenant_id"), (self.asset_id, "asset_id"), (self.content_sha256, "content_sha256")):
            if not value.strip():
                raise VisualSearchIndexError(f"{name} must be non-empty")
        if len(self.content_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.content_sha256.casefold()):
            raise VisualSearchIndexError("content_sha256 must be a SHA-256 hex digest")

    @property
    def document_id(self) -> str:
        identity = (self.tenant_id, self.asset_id, self.content_sha256, self.embedding.descriptor.embedding_schema_version)
        return "visual:" + hashlib.sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    def to_document(self) -> dict[str, Any]:
        descriptor = self.embedding.descriptor
        return {
            "tenant_id": self.tenant_id, "asset_id": self.asset_id, "content_sha256": self.content_sha256,
            "embedding_schema_version": descriptor.embedding_schema_version,
            "encoder_name": descriptor.encoder_name, "encoder_revision": descriptor.encoder_revision,
            "preprocess_version": descriptor.preprocess_version, "similarity": descriptor.similarity,
            "visual_embedding": list(self.embedding.values), "source_id": self.source_id,
            "source_provider": self.source_provider, "media_kind": self.media_kind,
            "mime_type": self.mime_type, "extension": self.extension,
            "design_type": list(self.design_type), "ancestor_ids": list(self.ancestor_ids),
            "is_deleted": self.is_deleted, "is_hidden": self.is_hidden,
        }


@dataclass(frozen=True, slots=True)
class VisualSearchHit:
    document_id: str
    tenant_id: str
    asset_id: str
    score: float
    content_sha256: str


def visual_index_mapping(descriptor: EmbeddingDescriptor) -> dict[str, Any]:
    """Dedicated strict mapping. Search V3's mapping remains untouched."""
    keyword = {"type": "keyword"}
    return {"dynamic": "strict", "properties": {
        "tenant_id": keyword, "asset_id": keyword, "content_sha256": keyword,
        "embedding_schema_version": keyword, "encoder_name": keyword,
        "encoder_revision": keyword, "preprocess_version": keyword, "similarity": keyword,
        "visual_embedding": {"type": "dense_vector", "dims": descriptor.dimension, "index": True, "similarity": descriptor.similarity},
        "source_id": keyword, "source_provider": keyword, "media_kind": keyword,
        "mime_type": keyword, "extension": keyword, "design_type": keyword,
        "ancestor_ids": keyword, "is_deleted": {"type": "boolean"}, "is_hidden": {"type": "boolean"},
    }}


class VisualSearchElasticsearchIndex:
    """Versioned, isolated Elasticsearch KNN store for visual embeddings."""

    def __init__(self, config: ElasticsearchV3Config, descriptor: EmbeddingDescriptor, *, client: Any | None = None) -> None:
        self.descriptor = descriptor
        self._index = ElasticsearchV3Index(ElasticsearchV3Config(
            base_url=config.base_url, index_prefix=f"{config.index_prefix}-visual-{descriptor.embedding_schema_version}",
            request_timeout_seconds=config.request_timeout_seconds, bulk_batch_size=config.bulk_batch_size,
            index_generation=config.index_generation,
        ), client=client)

    @property
    def read_alias(self) -> str: return self._index.read_alias
    @property
    def write_alias(self) -> str: return self._index.write_alias
    def physical_index_name(self, version: str) -> str: return self._index.physical_index_name(version)
    def index_definition(self) -> dict[str, Any]: return {"settings": {}, "mappings": visual_index_mapping(self.descriptor)}
    async def aclose(self) -> None: await self._index.aclose()
    async def create_index(self, version: str) -> str:
        name = self.physical_index_name(version)
        await self._index._request("PUT", f"/{name}", json_body=self.index_definition())
        return name
    async def ensure_index(self, version: str) -> str:
        name = self.physical_index_name(version)
        if not await self._index._request("GET", f"/{name}/_settings", allow_not_found=True):
            await self.create_index(version)
        return name
    async def switch_aliases(self, target_index: str): return await self._index.switch_aliases(target_index)
    async def alias_indices(self) -> dict[str, set[str]]: return await self._index.alias_indices()

    async def upsert(self, document: VisualIndexDocument) -> None:
        self._validate_embedding(document.embedding)
        await self._index._request("PUT", f"/{self.write_alias}/_doc/{document.document_id}?refresh=wait_for", json_body=document.to_document())

    async def delete_asset(self, *, tenant_id: str, asset_id: str) -> int:
        scope = VisualSearchScope(tenant_id)
        if not asset_id.strip(): raise VisualSearchIndexError("asset_id must be non-empty")
        response = await self._index._request("POST", f"/{self.write_alias}/_delete_by_query?refresh=true", json_body={"query": {"bool": {"filter": [*scope.filters()[:1], {"term": {"asset_id": asset_id}}]}}})
        return int(response.get("deleted") or 0)

    def knn_query(self, embedding: VisualEmbedding, *, scope: VisualSearchScope, metadata_filters: VisualMetadataFilters | None = None, limit: int = 40, num_candidates: int | None = None) -> dict[str, Any]:
        self._validate_embedding(embedding)
        if not 1 <= limit <= _MAX_RESULTS: raise VisualSearchIndexError(f"limit must be between 1 and {_MAX_RESULTS}")
        candidates = num_candidates if num_candidates is not None else max(limit * 4, 100)
        if not limit <= candidates <= _MAX_CANDIDATES: raise VisualSearchIndexError("num_candidates must be between limit and 1000")
        filters = [*scope.filters(), *((metadata_filters or VisualMetadataFilters()).filters())]
        return {"size": limit, "_source": ["tenant_id", "asset_id", "content_sha256", "embedding_schema_version", "source_id", "source_provider", "media_kind", "mime_type", "extension", "design_type"], "knn": {"field": "visual_embedding", "query_vector": list(embedding.values), "k": limit, "num_candidates": candidates, "filter": filters}}

    async def search(self, embedding: VisualEmbedding, *, scope: VisualSearchScope, metadata_filters: VisualMetadataFilters | None = None, limit: int = 40, num_candidates: int | None = None) -> list[VisualSearchHit]:
        payload = await self._index._request("POST", f"/{self.read_alias}/_search", json_body=self.knn_query(embedding, scope=scope, metadata_filters=metadata_filters, limit=limit, num_candidates=num_candidates))
        return self._hits(payload, tenant_id=scope.tenant_id)

    @staticmethod
    def _hits(payload: Mapping[str, Any], *, tenant_id: str) -> list[VisualSearchHit]:
        raw_hits = payload.get("hits", {}).get("hits", [])
        if not isinstance(raw_hits, list): raise ElasticsearchV3RequestError("Elasticsearch returned malformed KNN hits")
        hits: list[VisualSearchHit] = []
        for raw in raw_hits:
            if not isinstance(raw, Mapping): continue
            source = raw.get("_source")
            if not isinstance(source, Mapping) or source.get("tenant_id") != tenant_id: continue
            asset_id, content_sha256, score = source.get("asset_id"), source.get("content_sha256"), raw.get("_score")
            if not isinstance(asset_id, str) or not isinstance(content_sha256, str) or not isinstance(score, (int, float)) or not isfinite(float(score)): continue
            hits.append(VisualSearchHit(str(raw.get("_id") or ""), tenant_id, asset_id, float(score), content_sha256))
        return hits

    def _validate_embedding(self, embedding: VisualEmbedding) -> None:
        descriptor, expected = embedding.descriptor, self.descriptor
        if (descriptor.embedding_schema_version != expected.embedding_schema_version or descriptor.dimension != expected.dimension or descriptor.encoder_name != expected.encoder_name or descriptor.encoder_revision != expected.encoder_revision or descriptor.preprocess_version != expected.preprocess_version or descriptor.similarity != expected.similarity):
            raise VisualSearchIndexError("embedding descriptor does not match visual index schema")
        if len(embedding.values) != expected.dimension or not all(isfinite(value) for value in embedding.values):
            raise VisualSearchIndexError("embedding must contain finite values for the configured dimension")
