from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

from app.infrastructure.search.elasticsearch_v2 import AliasSwitchResult, ElasticsearchV3Config, ElasticsearchV3Index, ElasticsearchV3RequestError
from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding

_MAX_RESULTS = 200
_MAX_CANDIDATES = 1_000
_MIN_INT8_HNSW_ELASTICSEARCH = (8, 15)


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
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class VisualVectorIndexOptions:
    """Explicit candidate-only vector index options."""

    type: str
    m: int = 16
    ef_construction: int = 100

    def __post_init__(self) -> None:
        if self.type != "int8_hnsw":
            raise VisualSearchIndexError("unsupported visual vector index option")
        if not 1 <= self.m <= 512:
            raise VisualSearchIndexError("HNSW m must be between 1 and 512")
        if not 1 <= self.ef_construction <= 10_000:
            raise VisualSearchIndexError(
                "HNSW ef_construction must be between 1 and 10000"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "m": self.m,
            "ef_construction": self.ef_construction,
        }


def elasticsearch_supports_int8_hnsw(version: str) -> bool:
    """Conservative project gate for the pinned Elastic 8.15+ feature set."""
    parts = version.strip().split(".")
    if len(parts) < 2:
        return False
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return (major, minor) >= _MIN_INT8_HNSW_ELASTICSEARCH


def visual_index_mapping(
    descriptor: EmbeddingDescriptor,
    *,
    vector_index_options: VisualVectorIndexOptions | None = None,
) -> dict[str, Any]:
    """Dedicated strict mapping. Search V3's mapping remains untouched."""
    keyword = {"type": "keyword"}
    vector: dict[str, Any] = {
        "type": "dense_vector",
        "dims": descriptor.dimension,
        "index": True,
        "similarity": descriptor.similarity,
    }
    if vector_index_options is not None:
        vector["index_options"] = vector_index_options.to_mapping()
    return {"dynamic": "strict", "properties": {
        "tenant_id": keyword, "asset_id": keyword, "content_sha256": keyword,
        "embedding_schema_version": keyword, "encoder_name": keyword,
        "encoder_revision": keyword, "preprocess_version": keyword, "similarity": keyword,
        "visual_embedding": vector,
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
    def write_alias(self) -> str: return self.read_alias
    def physical_index_name(self, version: str) -> str: return self._index.physical_index_name(version)
    def index_definition(
        self,
        *,
        vector_index_options: VisualVectorIndexOptions | None = None,
    ) -> dict[str, Any]:
        return {
            "settings": {},
            "mappings": visual_index_mapping(
                self.descriptor,
                vector_index_options=vector_index_options,
            ),
        }
    async def aclose(self) -> None: await self._index.aclose()
    async def create_index(
        self,
        version: str,
        *,
        vector_index_options: VisualVectorIndexOptions | None = None,
    ) -> str:
        name = self.physical_index_name(version)
        await self._index._request(
            "PUT",
            f"/{name}",
            json_body=self.index_definition(
                vector_index_options=vector_index_options,
            ),
        )
        return name
    async def elasticsearch_version(self) -> str:
        payload = await self._index._request("GET", "/")
        version = payload.get("version") if isinstance(payload, Mapping) else None
        number = version.get("number") if isinstance(version, Mapping) else None
        if not isinstance(number, str) or not number.strip():
            raise ElasticsearchV3RequestError(
                "Elasticsearch version response is malformed"
            )
        return number.strip()
    async def supports_int8_hnsw(self) -> bool:
        return elasticsearch_supports_int8_hnsw(
            await self.elasticsearch_version()
        )
    async def create_int8_hnsw_candidate(
        self,
        version: str,
        *,
        m: int = 16,
        ef_construction: int = 100,
    ) -> str:
        deployed_version = await self.elasticsearch_version()
        if not elasticsearch_supports_int8_hnsw(deployed_version):
            raise VisualSearchIndexError(
                "deployed Elasticsearch does not meet the int8_hnsw capability gate"
            )
        return await self.create_index(
            version,
            vector_index_options=VisualVectorIndexOptions(
                "int8_hnsw",
                m=m,
                ef_construction=ef_construction,
            ),
        )
    async def ensure_index(self, version: str) -> str:
        name = self.physical_index_name(version)
        if not await self._index._request("GET", f"/{name}/_settings", allow_not_found=True):
            await self.create_index(version)
        return name

    def _validate_physical_candidate_index(self, target_index: str) -> None:
        value = target_index.strip()
        prefix = (
            f"{self._index.config.index_prefix}-"
            f"{self._index.config.index_generation}-"
        )
        if (
            not value
            or value in {self.read_alias, self.write_alias}
            or not value.startswith(prefix)
            or len(value) > 255
            or any(
                char not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
                for char in value
            )
        ):
            raise VisualSearchIndexError(
                "candidate target must be a physical visual index in this namespace"
            )

    async def reindex_active_to_candidate(
        self,
        target_index: str,
        *,
        max_docs: int | None = None,
    ) -> int:
        """Copy the current projection into a physical candidate without alias mutation."""
        self._validate_physical_candidate_index(target_index)
        if max_docs is not None and not 1 <= max_docs <= 1_000_000:
            raise VisualSearchIndexError("max_docs must be between 1 and 1000000")
        await self._index._request("HEAD", f"/{target_index}")
        body: dict[str, Any] = {
            "source": {"index": self.read_alias},
            "dest": {"index": target_index, "op_type": "create"},
            "conflicts": "abort",
        }
        if max_docs is not None:
            body["max_docs"] = max_docs
        response = await self._index._request(
            "POST",
            "/_reindex?refresh=true&wait_for_completion=true",
            json_body=body,
        )
        failures = response.get("failures") if isinstance(response, Mapping) else None
        if failures:
            raise ElasticsearchV3RequestError(
                "Elasticsearch candidate reindex returned failures"
            )
        created = response.get("created") if isinstance(response, Mapping) else None
        if not isinstance(created, int) or created < 0:
            raise ElasticsearchV3RequestError(
                "Elasticsearch candidate reindex response is malformed"
            )
        return created

    async def _target_kind(self) -> str:
        alias_payload = await self._index._request(
            "GET",
            f"/_alias/{self.read_alias}",
            allow_not_found=True,
        )
        if alias_payload:
            return "alias"
        index_payload = await self._index._request(
            "GET",
            f"/{self.read_alias}/_settings",
            allow_not_found=True,
        )
        return "physical" if index_payload else "missing"

    def _validate_candidate_mapping(
        self,
        target_index: str,
        mapping_payload: Mapping[str, Any],
    ) -> None:
        index_payload = mapping_payload.get(target_index)
        mappings = (
            index_payload.get("mappings")
            if isinstance(index_payload, Mapping)
            else None
        )
        properties = (
            mappings.get("properties")
            if isinstance(mappings, Mapping)
            else None
        )
        vector = (
            properties.get("visual_embedding")
            if isinstance(properties, Mapping)
            else None
        )
        if (
            not isinstance(mappings, Mapping)
            or mappings.get("dynamic") != "strict"
            or not isinstance(properties, Mapping)
            or properties.get("tenant_id") != {"type": "keyword"}
            or properties.get("asset_id") != {"type": "keyword"}
            or not isinstance(vector, Mapping)
            or vector.get("type") != "dense_vector"
            or vector.get("dims") != self.descriptor.dimension
            or vector.get("similarity") != self.descriptor.similarity
        ):
            raise VisualSearchIndexError(
                "candidate mapping is incompatible with the active visual schema"
            )

    async def migrate_legacy_physical_to_alias(
        self,
        target_index: str,
        *,
        backup_index: str,
    ) -> AliasSwitchResult:
        """Promote a strict candidate when the historical read target is an index.

        The first Visual Search canary used the future read-alias name as a
        physical index. Elasticsearch does not allow an alias and index to share
        a name, so the safe transition is:

        1. copy the legacy physical index to a separately named rollback index;
        2. require source, rollback and candidate counts to match;
        3. atomically remove the legacy physical index and add the read alias to
           the candidate in one alias cluster-state update.

        Callers must drain visual-index writers before invoking this method.
        Any failed precondition leaves the active physical index untouched.
        """
        self._validate_physical_candidate_index(target_index)
        self._validate_physical_candidate_index(backup_index)
        if target_index == backup_index:
            raise VisualSearchIndexError(
                "candidate and rollback backup must be different indices"
            )
        if await self._target_kind() != "physical":
            raise VisualSearchIndexError(
                "legacy migration requires the visual read target to be a physical index"
            )

        target_mapping = await self._index._request(
            "GET",
            f"/{target_index}/_mapping",
        )
        self._validate_candidate_mapping(target_index, target_mapping)

        existing_backup = await self._index._request(
            "GET",
            f"/{backup_index}/_settings",
            allow_not_found=True,
        )
        if existing_backup:
            raise VisualSearchIndexError(
                "rollback backup index already exists"
            )

        source_mapping_payload = await self._index._request(
            "GET",
            f"/{self.read_alias}/_mapping",
        )
        source_payload = source_mapping_payload.get(self.read_alias)
        source_mappings = (
            source_payload.get("mappings")
            if isinstance(source_payload, Mapping)
            else None
        )
        if not isinstance(source_mappings, Mapping):
            raise ElasticsearchV3RequestError(
                "legacy visual index mapping response is malformed"
            )

        source_count_payload = await self._index._request(
            "GET",
            f"/{self.read_alias}/_count",
        )
        source_count = source_count_payload.get("count")
        if not isinstance(source_count, int) or source_count < 0:
            raise ElasticsearchV3RequestError(
                "legacy visual index count response is malformed"
            )

        await self._index._request(
            "PUT",
            f"/{backup_index}",
            json_body={"settings": {}, "mappings": dict(source_mappings)},
        )
        backup_copy = await self._index._request(
            "POST",
            "/_reindex?refresh=true&wait_for_completion=true",
            json_body={
                "source": {"index": self.read_alias},
                "dest": {"index": backup_index, "op_type": "create"},
                "conflicts": "abort",
            },
        )
        failures = (
            backup_copy.get("failures")
            if isinstance(backup_copy, Mapping)
            else None
        )
        backup_created = (
            backup_copy.get("created")
            if isinstance(backup_copy, Mapping)
            else None
        )
        if failures or backup_created != source_count:
            raise ElasticsearchV3RequestError(
                "legacy visual rollback backup did not copy the full source"
            )

        # Rebuild the inactive candidate from the drained source immediately
        # before cutover. This removes stale candidate documents and guarantees
        # that a successful migration promotes the same projection we backed up.
        await self._index._request(
            "POST",
            f"/{target_index}/_delete_by_query?refresh=true&conflicts=proceed",
            json_body={"query": {"match_all": {}}},
        )
        target_copy = await self._index._request(
            "POST",
            "/_reindex?refresh=true&wait_for_completion=true",
            json_body={
                "source": {"index": self.read_alias},
                "dest": {"index": target_index, "op_type": "create"},
                "conflicts": "abort",
            },
        )
        target_failures = (
            target_copy.get("failures")
            if isinstance(target_copy, Mapping)
            else None
        )
        target_created = (
            target_copy.get("created")
            if isinstance(target_copy, Mapping)
            else None
        )
        if target_failures or target_created != source_count:
            raise ElasticsearchV3RequestError(
                "strict visual candidate did not copy the full source"
            )

        source_after_payload = await self._index._request(
            "GET",
            f"/{self.read_alias}/_count",
        )
        backup_count_payload = await self._index._request(
            "GET",
            f"/{backup_index}/_count",
        )
        target_count_payload = await self._index._request(
            "GET",
            f"/{target_index}/_count",
        )
        source_after = source_after_payload.get("count")
        backup_count = backup_count_payload.get("count")
        target_count = target_count_payload.get("count")
        if (
            source_after != source_count
            or backup_count != source_count
            or target_count != source_count
        ):
            raise VisualSearchIndexError(
                "visual index counts changed or do not match; cutover aborted"
            )

        await self._index._request(
            "POST",
            "/_aliases",
            json_body={
                "actions": [
                    {"remove_index": {"index": self.read_alias}},
                    {
                        "add": {
                            "index": target_index,
                            "alias": self.read_alias,
                            "is_write_index": True,
                        }
                    },
                ]
            },
        )
        active = await self._index._request(
            "GET",
            f"/_alias/{self.read_alias}",
        )
        if set(active) != {target_index}:
            raise ElasticsearchV3RequestError(
                "visual read alias verification failed after cutover"
            )
        return AliasSwitchResult(
            target_index,
            (backup_index,),
            (),
        )

    async def switch_aliases(self, target_index: str) -> AliasSwitchResult:
        """Atomically move the dedicated visual read/write target."""
        self._validate_physical_candidate_index(target_index)
        await self._index._request("HEAD", f"/{target_index}")
        current = await self._index._alias_indices()
        if not current["read"]:
            raise VisualSearchIndexError(
                "visual read alias is not established; use the legacy migration path"
            )
        actions = [
            {"remove": {"index": name, "alias": self.read_alias, "must_exist": True}}
            for name in sorted(current["read"])
        ]
        actions.append(
            {
                "add": {
                    "index": target_index,
                    "alias": self.read_alias,
                    "is_write_index": True,
                }
            }
        )
        await self._index._request("POST", "/_aliases", json_body={"actions": actions})
        return AliasSwitchResult(target_index, tuple(sorted(current["read"])), ())
    async def alias_indices(self) -> dict[str, set[str]]: return await self._index.alias_indices()

    async def upsert(self, document: VisualIndexDocument) -> None:
        self._validate_embedding(document.embedding)
        await self._index._request("PUT", f"/{self.read_alias}/_doc/{document.document_id}?refresh=wait_for", json_body=document.to_document())

    async def get_document(self, document_id: str) -> Mapping[str, Any]:
        if not document_id.strip():
            raise VisualSearchIndexError("document_id must be non-empty")
        return await self._index._request("GET", f"/{self.read_alias}/_doc/{document_id}")

    async def delete_asset(self, *, tenant_id: str, asset_id: str) -> int:
        scope = VisualSearchScope(tenant_id)
        if not asset_id.strip(): raise VisualSearchIndexError("asset_id must be non-empty")
        response = await self._index._request("POST", f"/{self.read_alias}/_delete_by_query?refresh=true", json_body={"query": {"bool": {"filter": [*scope.filters()[:1], {"term": {"asset_id": asset_id}}]}}})
        return int(response.get("deleted") or 0)

    def knn_query(self, embedding: VisualEmbedding, *, scope: VisualSearchScope, metadata_filters: VisualMetadataFilters | None = None, limit: int = 40, num_candidates: int | None = None, exclude_asset_id: str | None = None) -> dict[str, Any]:
        self._validate_embedding(embedding)
        if not 1 <= limit <= _MAX_RESULTS: raise VisualSearchIndexError(f"limit must be between 1 and {_MAX_RESULTS}")
        candidates = num_candidates if num_candidates is not None else max(limit * 4, 100)
        if not limit <= candidates <= _MAX_CANDIDATES: raise VisualSearchIndexError("num_candidates must be between limit and 1000")
        filters = [*scope.filters(), *((metadata_filters or VisualMetadataFilters()).filters())]
        if exclude_asset_id:
            filters.append({"bool": {"must_not": [{"term": {"asset_id": exclude_asset_id}}]}})
        return {"size": limit, "_source": ["tenant_id", "asset_id", "content_sha256", "embedding_schema_version", "source_id", "source_provider", "media_kind", "mime_type", "extension", "design_type"], "knn": {"field": "visual_embedding", "query_vector": list(embedding.values), "k": limit, "num_candidates": candidates, "filter": filters}}

    async def scan_projection_metadata(self, tenant_id: str, *, page_size: int = 500) -> list[dict[str, Any]]:
        """Read-only tenant projection scan; intentionally excludes vectors."""
        if not tenant_id.strip(): raise VisualSearchIndexError("visual search requires a tenant scope")
        if not 1 <= page_size <= 1000: raise VisualSearchIndexError("projection scan page_size must be between 1 and 1000")
        after = None; rows = []; legacy_keyword_fields = False
        while True:
            suffix = ".keyword" if legacy_keyword_fields else ""
            body = {"size": page_size, "_source": ["tenant_id", "asset_id", "content_sha256", "embedding_schema_version", "encoder_name", "encoder_revision", "preprocess_version", "similarity", "is_deleted", "is_hidden"], "query": {"bool": {"filter": [{"term": {f"tenant_id{suffix}": tenant_id}}]}}, "sort": [{f"asset_id{suffix}": "asc"}, {f"content_sha256{suffix}": "asc"}, {f"embedding_schema_version{suffix}": "asc"}]}
            if after is not None: body["search_after"] = after
            try:
                payload = await self._index._request("POST", f"/{self.read_alias}/_search", json_body=body)
            except ElasticsearchV3RequestError as exc:
                # The first production canary was allowed to auto-create the
                # read target, so exact fields received dynamic text+keyword
                # mappings. Keep the read-only coverage scan operable while a
                # strict replacement index is prepared. New strict mappings
                # continue to use the canonical bare keyword fields.
                if after is not None or legacy_keyword_fields or exc.status_code != 400:
                    raise
                legacy_keyword_fields = True
                continue
            hits = payload.get("hits", {}).get("hits", [])
            if not isinstance(hits, list): raise ElasticsearchV3RequestError("Elasticsearch returned malformed projection scan")
            rows.extend(dict(hit.get("_source") or {}) for hit in hits if isinstance(hit, Mapping) and isinstance(hit.get("_source"), Mapping) and hit["_source"].get("tenant_id") == tenant_id)
            if len(hits) < page_size: return rows
            after = hits[-1].get("sort")
            if not isinstance(after, list): raise ElasticsearchV3RequestError("Elasticsearch projection scan cursor missing")

    async def search_index(
        self,
        target_index: str,
        embedding: VisualEmbedding,
        *,
        scope: VisualSearchScope,
        metadata_filters: VisualMetadataFilters | None = None,
        limit: int = 40,
        num_candidates: int | None = None,
        exclude_asset_id: str | None = None,
    ) -> list[VisualSearchHit]:
        """Search a physical candidate index without changing the active alias."""
        self._validate_physical_candidate_index(target_index)
        payload = await self._index._request(
            "POST",
            f"/{target_index}/_search",
            json_body=self.knn_query(
                embedding,
                scope=scope,
                metadata_filters=metadata_filters,
                limit=limit,
                num_candidates=num_candidates,
                exclude_asset_id=exclude_asset_id,
            ),
        )
        return self._hits(payload, tenant_id=scope.tenant_id)

    async def search(self, embedding: VisualEmbedding, *, scope: VisualSearchScope, metadata_filters: VisualMetadataFilters | None = None, limit: int = 40, num_candidates: int | None = None, exclude_asset_id: str | None = None) -> list[VisualSearchHit]:
        payload = await self._index._request("POST", f"/{self.read_alias}/_search", json_body=self.knn_query(embedding, scope=scope, metadata_filters=metadata_filters, limit=limit, num_candidates=num_candidates, exclude_asset_id=exclude_asset_id))
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
            hits.append(VisualSearchHit(str(raw.get("_id") or ""), tenant_id, asset_id, float(score), content_sha256, str(source.get("source_id") or "") or None))
        return hits

    def _validate_embedding(self, embedding: VisualEmbedding) -> None:
        descriptor, expected = embedding.descriptor, self.descriptor
        if (descriptor.embedding_schema_version != expected.embedding_schema_version or descriptor.dimension != expected.dimension or descriptor.encoder_name != expected.encoder_name or descriptor.encoder_revision != expected.encoder_revision or descriptor.preprocess_version != expected.preprocess_version or descriptor.similarity != expected.similarity):
            raise VisualSearchIndexError("embedding descriptor does not match visual index schema")
        if len(embedding.values) != expected.dimension or not all(isfinite(value) for value in embedding.values):
            raise VisualSearchIndexError("embedding must contain finite values for the configured dimension")
