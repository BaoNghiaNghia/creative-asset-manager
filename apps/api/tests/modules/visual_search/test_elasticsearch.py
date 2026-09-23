from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from app.infrastructure.search.elasticsearch_v2 import (
    ElasticsearchV3Config,
    ElasticsearchV3RequestError,
)
from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.modules.visual_search.elasticsearch import (
    VisualIndexDocument,
    VisualMetadataFilters,
    VisualSearchElasticsearchIndex,
    VisualSearchIndexError,
    VisualSearchScope,
    VisualVectorIndexOptions,
    elasticsearch_supports_int8_hnsw,
)
from app.modules.visual_search.model_spec import VISUAL_SEARCH_V1_DESCRIPTOR, VISUAL_SEARCH_V2_DESCRIPTOR
from app.modules.visual_search.repository import VisualSearchRepository

DESCRIPTOR = EmbeddingDescriptor("siglip", "revision-1", "visual_embedding_v1", 3, "siglip-test-v1")
EMBEDDING = VisualEmbedding(DESCRIPTOR, (0.1, 0.2, 0.3))


class VisualSearchElasticsearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.index = VisualSearchElasticsearchIndex(ElasticsearchV3Config("http://elasticsearch.test", index_prefix="creative-assets", index_generation="v3"), DESCRIPTOR)

    def test_mapping_is_strict_and_vector_is_versioned(self) -> None:
        mapping = self.index.index_definition()["mappings"]
        self.assertEqual(mapping["dynamic"], "strict")
        self.assertEqual(mapping["properties"]["visual_embedding"], {"type": "dense_vector", "dims": 3, "index": True, "similarity": "cosine"})
        self.assertNotIn("visual_embedding", self.index._index.index_definition()["mappings"]["properties"])

    def test_aliases_are_isolated_from_search_v3(self) -> None:
        self.assertEqual(self.index.read_alias, "creative-assets-visual-visual_embedding_v1-v3-read")
        self.assertNotEqual(self.index.read_alias, "creative-assets-v3-read")

    def test_int8_hnsw_candidate_mapping_is_explicit_and_baseline_stays_unchanged(self) -> None:
        baseline = self.index.index_definition()["mappings"]["properties"]["visual_embedding"]
        candidate = self.index.index_definition(
            vector_index_options=VisualVectorIndexOptions(
                "int8_hnsw",
                m=32,
                ef_construction=200,
            )
        )["mappings"]["properties"]["visual_embedding"]
        self.assertNotIn("index_options", baseline)
        self.assertEqual(
            candidate["index_options"],
            {
                "type": "int8_hnsw",
                "m": 32,
                "ef_construction": 200,
            },
        )

    def test_int8_hnsw_version_gate_is_conservative(self) -> None:
        self.assertTrue(elasticsearch_supports_int8_hnsw("8.15.3"))
        self.assertTrue(elasticsearch_supports_int8_hnsw("9.0.0"))
        self.assertFalse(elasticsearch_supports_int8_hnsw("8.14.9"))
        self.assertFalse(elasticsearch_supports_int8_hnsw("invalid"))

    def test_siglip_v1_and_siglip2_v2_use_distinct_index_namespaces(self) -> None:
        config = ElasticsearchV3Config("http://elasticsearch.test", index_prefix="creative-assets", index_generation="v3")
        v1 = VisualSearchElasticsearchIndex(config, VISUAL_SEARCH_V1_DESCRIPTOR)
        v2 = VisualSearchElasticsearchIndex(config, VISUAL_SEARCH_V2_DESCRIPTOR)
        self.assertNotEqual(v1.read_alias, v2.read_alias)
        self.assertIn("visual_embedding_v1", v1.read_alias)
        self.assertIn("visual_embedding_v2", v2.read_alias)

    def test_document_identity_changes_for_content_or_schema(self) -> None:
        document = VisualIndexDocument("tenant-a", "asset-a", "a" * 64, EMBEDDING)
        changed_content = VisualIndexDocument("tenant-a", "asset-a", "b" * 64, EMBEDDING)
        changed_schema = VisualIndexDocument("tenant-a", "asset-a", "a" * 64, VisualEmbedding(replace(DESCRIPTOR, embedding_schema_version="v2"), (0.1, 0.2, 0.3)))
        self.assertNotEqual(document.document_id, changed_content.document_id)
        self.assertNotEqual(document.document_id, changed_schema.document_id)

    def test_knn_query_enforces_tenant_and_lifecycle_filters(self) -> None:
        body = self.index.knn_query(EMBEDDING, scope=VisualSearchScope("tenant-a", ({"term": {"source_id": "source-a"}},)), metadata_filters=VisualMetadataFilters(media_kind="image", extension="jpg"), limit=5, num_candidates=12)
        filters = body["knn"]["filter"]
        for required in ({"term": {"tenant_id": "tenant-a"}}, {"term": {"is_deleted": False}}, {"term": {"is_hidden": False}}, {"term": {"source_id": "source-a"}}, {"term": {"media_kind": "image"}}):
            self.assertIn(required, filters)
        self.assertNotIn("visual_embedding", body["_source"])

    def test_knn_rejects_empty_tenant_and_wrong_descriptor(self) -> None:
        with self.assertRaises(VisualSearchIndexError): VisualSearchScope(" ")
        with self.assertRaises(VisualSearchIndexError):
            self.index.knn_query(VisualEmbedding(replace(DESCRIPTOR, dimension=2), (0.1, 0.2)), scope=VisualSearchScope("tenant-a"))

    def test_search_defensively_drops_cross_tenant_hit(self) -> None:
        async def verify() -> None:
            self.index._index._request = AsyncMock(return_value={"hits": {"hits": [
                {"_id": "good", "_score": 1.0, "_source": {"tenant_id": "tenant-a", "asset_id": "asset-a", "content_sha256": "a" * 64}},
                {"_id": "bad", "_score": 9.0, "_source": {"tenant_id": "tenant-b", "asset_id": "asset-b", "content_sha256": "b" * 64}},
            ]}})
            hits = await VisualSearchRepository(self.index).nearest(EMBEDDING, scope=VisualSearchScope("tenant-a"))
            self.assertEqual([hit.asset_id for hit in hits], ["asset-a"])
        asyncio.run(verify())

    def test_projection_metadata_scan_is_tenant_scoped_and_paginated(self) -> None:
        async def verify() -> None:
            self.index._index._request = AsyncMock(side_effect=[{"hits":{"hits":[{"_source":{"tenant_id":"tenant-a","asset_id":"a"},"sort":["a","1"]},{"_source":{"tenant_id":"tenant-b","asset_id":"x"},"sort":["x","2"]}]}},{"hits":{"hits":[{"_source":{"tenant_id":"tenant-a","asset_id":"b"},"sort":["b","3"]}]}}])
            rows=await self.index.scan_projection_metadata("tenant-a",page_size=2)
            self.assertEqual([r["asset_id"] for r in rows],["a","b"])
            self.assertEqual(self.index._index._request.await_count,2)
            body=self.index._index._request.await_args_list[0].kwargs["json_body"]
            self.assertIn({"term":{"tenant_id":"tenant-a"}},body["query"]["bool"]["filter"])
            self.assertNotIn("visual_embedding",body["_source"])
            self.assertEqual(self.index._index._request.await_args_list[1].kwargs["json_body"]["search_after"],["x","2"])
        asyncio.run(verify())

    def test_projection_metadata_scan_falls_back_for_legacy_dynamic_mapping(self) -> None:
        async def verify() -> None:
            self.index._index._request = AsyncMock(side_effect=[
                ElasticsearchV3RequestError("bad sort mapping", status_code=400),
                {"hits": {"hits": [{
                    "_source": {"tenant_id": "tenant-a", "asset_id": "a"},
                    "sort": ["a", "1", "visual_embedding_v1"],
                }]}},
            ])

            rows = await self.index.scan_projection_metadata("tenant-a", page_size=2)

            self.assertEqual([row["asset_id"] for row in rows], ["a"])
            fallback = self.index._index._request.await_args_list[1].kwargs["json_body"]
            self.assertIn(
                {"term": {"tenant_id.keyword": "tenant-a"}},
                fallback["query"]["bool"]["filter"],
            )
            self.assertEqual(
                fallback["sort"],
                [
                    {"asset_id.keyword": "asc"},
                    {"content_sha256.keyword": "asc"},
                    {"embedding_schema_version.keyword": "asc"},
                ],
            )

        asyncio.run(verify())

    def test_upsert_and_delete_are_tenant_scoped(self) -> None:
        async def verify() -> None:
            self.index._index._request = AsyncMock(return_value={"deleted": 1})
            document = VisualIndexDocument("tenant-a", "asset-a", "a" * 64, EMBEDDING)
            await self.index.upsert(document)
            self.assertIn(document.document_id, self.index._index._request.await_args.args[1])
            self.assertEqual(self.index._index._request.await_args.kwargs["json_body"]["tenant_id"], "tenant-a")
            await self.index.delete_asset(tenant_id="tenant-a", asset_id="asset-a")
            self.assertIn({"term": {"tenant_id": "tenant-a"}}, self.index._index._request.await_args.kwargs["json_body"]["query"]["bool"]["filter"])
        asyncio.run(verify())



    def test_create_int8_hnsw_candidate_probes_version_and_does_not_switch_alias(self) -> None:
        async def verify() -> None:
            target = self.index.physical_index_name("20260922-int8-hnsw")
            self.index._index._request = AsyncMock(
                side_effect=[
                    {"version": {"number": "8.15.3"}},
                    {},
                ]
            )
            created = await self.index.create_int8_hnsw_candidate(
                "20260922-int8-hnsw",
                m=24,
                ef_construction=150,
            )
            self.assertEqual(created, target)
            put = self.index._index._request.await_args_list[1]
            self.assertEqual(put.args[:2], ("PUT", f"/{target}"))
            vector = put.kwargs["json_body"]["mappings"]["properties"]["visual_embedding"]
            self.assertEqual(
                vector["index_options"],
                {"type": "int8_hnsw", "m": 24, "ef_construction": 150},
            )
            self.assertNotIn("/_aliases", [call.args[1] for call in self.index._index._request.await_args_list])
        asyncio.run(verify())

    def test_reindex_and_candidate_search_stay_on_physical_index(self) -> None:
        async def verify() -> None:
            target = self.index.physical_index_name("20260922-ann")
            self.index._index._request = AsyncMock(
                side_effect=[
                    {},
                    {"created": 2, "failures": []},
                    {"hits": {"hits": [
                        {
                            "_id": "candidate-hit",
                            "_score": 1.0,
                            "_source": {
                                "tenant_id": "tenant-a",
                                "asset_id": "asset-a",
                                "content_sha256": "a" * 64,
                            },
                        }
                    ]}},
                ]
            )
            created = await self.index.reindex_active_to_candidate(target)
            self.assertEqual(created, 2)
            reindex = self.index._index._request.await_args_list[1]
            self.assertEqual(reindex.args[1], "/_reindex?refresh=true&wait_for_completion=true")
            self.assertEqual(reindex.kwargs["json_body"]["source"]["index"], self.index.read_alias)
            self.assertEqual(reindex.kwargs["json_body"]["dest"]["index"], target)

            hits = await self.index.search_index(
                target,
                EMBEDDING,
                scope=VisualSearchScope("tenant-a"),
                limit=1,
                num_candidates=1,
            )
            self.assertEqual([hit.asset_id for hit in hits], ["asset-a"])
            self.assertEqual(
                self.index._index._request.await_args_list[2].args[1],
                f"/{target}/_search",
            )
        asyncio.run(verify())

    def test_switch_aliases_uses_single_visual_read_alias(self) -> None:
        async def verify() -> None:
            target = self.index.physical_index_name("20260910-keyword")
            self.index._index._request = AsyncMock(side_effect=[
                {},
                {"old-index": {"aliases": {self.index.read_alias: {}}}},
                {},
                {},
            ])
            result = await self.index.switch_aliases(target)
            self.assertEqual(result.target_index, target)
            action_request = self.index._index._request.await_args_list[-1]
            actions = action_request.kwargs["json_body"]["actions"]
            self.assertEqual(actions, [
                {"remove": {"index": "old-index", "alias": self.index.read_alias, "must_exist": True}},
                {"add": {"index": target, "alias": self.index.read_alias, "is_write_index": True}},
            ])
        asyncio.run(verify())

    def test_migrate_legacy_physical_to_alias_preserves_rollback_copy_and_switches_atomically(self) -> None:
        async def verify() -> None:
            target = self.index.physical_index_name("20260923-strict")
            backup = self.index.physical_index_name("20260923-legacy-backup")
            legacy = self.index.read_alias
            candidate_mapping = {
                target: {
                    "mappings": {
                        "dynamic": "strict",
                        "properties": {
                            "tenant_id": {"type": "keyword"},
                            "asset_id": {"type": "keyword"},
                            "visual_embedding": {
                                "type": "dense_vector",
                                "dims": 3,
                                "similarity": "cosine",
                            },
                        },
                    }
                }
            }
            legacy_mapping = {
                legacy: {
                    "mappings": {
                        "properties": {
                            "tenant_id": {"type": "text"},
                            "asset_id": {"type": "text"},
                            "visual_embedding": {
                                "type": "dense_vector",
                                "dims": 3,
                                "similarity": "cosine",
                            },
                        }
                    }
                }
            }
            self.index._index._request = AsyncMock(
                side_effect=[
                    {},
                    {legacy: {"settings": {}}},
                    candidate_mapping,
                    {},
                    legacy_mapping,
                    {"count": 2},
                    {},
                    {"created": 2, "failures": []},
                    {"deleted": 2},
                    {"created": 2, "failures": []},
                    {"count": 2},
                    {"count": 2},
                    {"count": 2},
                    {},
                    {target: {"aliases": {legacy: {"is_write_index": True}}}},
                ]
            )

            result = await self.index.migrate_legacy_physical_to_alias(
                target,
                backup_index=backup,
            )

            self.assertEqual(result.target_index, target)
            self.assertEqual(result.previous_read_indices, (backup,))
            requests = self.index._index._request.await_args_list
            self.assertEqual(
                requests[6].kwargs["json_body"]["mappings"],
                legacy_mapping[legacy]["mappings"],
            )
            self.assertEqual(
                requests[8].args[1],
                f"/{target}/_delete_by_query?refresh=true&conflicts=proceed",
            )
            self.assertEqual(
                requests[9].kwargs["json_body"]["dest"],
                {"index": target, "op_type": "create"},
            )
            alias_request = requests[13]
            self.assertEqual(alias_request.args[:2], ("POST", "/_aliases"))
            self.assertEqual(
                alias_request.kwargs["json_body"]["actions"],
                [
                    {"remove_index": {"index": legacy}},
                    {
                        "add": {
                            "index": target,
                            "alias": legacy,
                            "is_write_index": True,
                        }
                    },
                ],
            )

        asyncio.run(verify())

    def test_migrate_legacy_physical_to_alias_aborts_before_alias_mutation_when_counts_drift(self) -> None:
        async def verify() -> None:
            target = self.index.physical_index_name("20260923-strict")
            backup = self.index.physical_index_name("20260923-legacy-backup")
            legacy = self.index.read_alias
            candidate_mapping = {
                target: {
                    "mappings": {
                        "dynamic": "strict",
                        "properties": {
                            "tenant_id": {"type": "keyword"},
                            "asset_id": {"type": "keyword"},
                            "visual_embedding": {
                                "type": "dense_vector",
                                "dims": 3,
                                "similarity": "cosine",
                            },
                        },
                    }
                }
            }
            self.index._index._request = AsyncMock(
                side_effect=[
                    {},
                    {legacy: {"settings": {}}},
                    candidate_mapping,
                    {},
                    {legacy: {"mappings": {"properties": {}}}},
                    {"count": 2},
                    {},
                    {"created": 2, "failures": []},
                    {"deleted": 2},
                    {"created": 2, "failures": []},
                    {"count": 3},
                    {"count": 2},
                    {"count": 2},
                ]
            )

            with self.assertRaisesRegex(
                VisualSearchIndexError,
                "counts changed or do not match",
            ):
                await self.index.migrate_legacy_physical_to_alias(
                    target,
                    backup_index=backup,
                )

            self.assertNotIn(
                "/_aliases",
                [call.args[1] for call in self.index._index._request.await_args_list],
            )

        asyncio.run(verify())
if __name__ == "__main__":
    unittest.main()
