from __future__ import annotations

import os
import unittest
from uuid import uuid4

from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualIndexDocument, VisualSearchElasticsearchIndex, VisualSearchScope

URL = os.getenv("INTEGRATION_ELASTICSEARCH_URL", "")
DESCRIPTOR = EmbeddingDescriptor("integration", "v1", "visual_embedding_v1", 3, "integration-v1")


@unittest.skipUnless(URL.startswith("http"), "real Elasticsearch required")
class VisualRealElasticsearchTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.prefix = "cam-visual-" + uuid4().hex[:10]
        self.index = VisualSearchElasticsearchIndex(ElasticsearchV3Config(URL, index_prefix=self.prefix, index_generation="v3"), DESCRIPTOR)
        self.physical = await self.index.create_index("000001")
        await self.index.switch_aliases(self.physical)

    async def asyncTearDown(self) -> None:
        try:
            response = await self.index._index.client.delete(f"/{self.prefix}-visual-visual_embedding_v1-v3-*", params={"expand_wildcards": "all"})
            response.raise_for_status()
        finally:
            await self.index.aclose()

    async def test_knn_mapping_alias_and_tenant_filter(self) -> None:
        mapping = await self.index._index.index_mapping(self.physical)
        self.assertEqual(mapping[self.physical]["mappings"]["properties"]["visual_embedding"]["type"], "dense_vector")
        await self.index.upsert(VisualIndexDocument("tenant-a", "asset-a", "a" * 64, VisualEmbedding(DESCRIPTOR, (1.0, 0.0, 0.0))))
        await self.index.upsert(VisualIndexDocument("tenant-b", "asset-b", "b" * 64, VisualEmbedding(DESCRIPTOR, (1.0, 0.0, 0.0))))
        hits = await self.index.search(VisualEmbedding(DESCRIPTOR, (1.0, 0.0, 0.0)), scope=VisualSearchScope("tenant-a"), limit=10, num_candidates=10)
        self.assertEqual([hit.asset_id for hit in hits], ["asset-a"])


if __name__ == "__main__":
    unittest.main()
