from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualIndexDocument, VisualMetadataFilters, VisualSearchElasticsearchIndex, VisualSearchIndexError, VisualSearchScope
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


if __name__ == "__main__":
    unittest.main()
