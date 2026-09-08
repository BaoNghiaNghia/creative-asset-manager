from __future__ import annotations

import io

from PIL import Image

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding
from app.operations.visual_search_benchmark import benchmark


class FakeEncoder:
    descriptor = EmbeddingDescriptor(
        encoder_name="fake",
        encoder_revision="v1",
        embedding_schema_version="visual_embedding_v1",
        dimension=2,
        preprocess_version="rgb-v1",
    )

    def encode_image(self, image):
        return VisualEmbedding(
            self.descriptor, (float(image.width), float(image.height))
        )


def test_benchmark_reports_descriptor_and_latency_metrics() -> None:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="PNG")
    result = benchmark(FakeEncoder, image_content=output.getvalue(), iterations=3)
    assert result["descriptor"]["dimension"] == 2
    assert result["sequential_count"] == 3
    assert result["single_encode_ms"] >= 0
