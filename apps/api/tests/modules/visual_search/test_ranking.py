from __future__ import annotations

import unittest

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.modules.visual_search.elasticsearch import VisualSearchHit
from app.modules.visual_search.ranking import VisualRankingWeights, diversify_hits, fuse_embeddings


class VisualRankingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.descriptor = EmbeddingDescriptor(
            encoder_name="test", encoder_revision="r1",
            embedding_schema_version="v1", dimension=2, preprocess_version="p1",
        )

    def test_fusion_normalizes_shared_embeddings(self) -> None:
        image = VisualEmbedding(self.descriptor, (1.0, 0.0))
        text = VisualEmbedding(self.descriptor, (0.0, 1.0))
        result = fuse_embeddings(
            image, text, weights=VisualRankingWeights(image=0.8, text=0.2)
        )
        self.assertAlmostEqual(sum(item * item for item in result.values), 1.0)
        self.assertGreater(result.values[0], result.values[1])

    def test_diversity_suppresses_exact_content_and_limits_source(self) -> None:
        hits = [
            VisualSearchHit("1", "tenant", "a", 1.0, "a" * 64, "source-1"),
            VisualSearchHit("2", "tenant", "b", 0.9, "a" * 64, "source-2"),
            VisualSearchHit("3", "tenant", "c", 0.8, "b" * 64, "source-1"),
            VisualSearchHit("4", "tenant", "d", 0.7, "c" * 64, "source-1"),
            VisualSearchHit("5", "tenant", "e", 0.6, "d" * 64, "source-2"),
        ]
        result = diversify_hits(hits, weights=VisualRankingWeights(max_per_source=2))
        self.assertEqual([item.asset_id for item in result], ["a", "c", "e"])


if __name__ == "__main__":
    unittest.main()
