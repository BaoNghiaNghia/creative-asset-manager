from __future__ import annotations

import asyncio
from base64 import b64encode
from io import BytesIO
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.model_spec import VISUAL_SEARCH_ACTIVE_DESCRIPTOR
from embedding_cache import BoundedEmbeddingCache
import main


class FakeEncoder:
    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR

    def __init__(self) -> None:
        self.text_calls = 0
        self.image_calls = 0

    def encode_text(self, _text: str) -> VisualEmbedding:
        self.text_calls += 1
        return VisualEmbedding(
            self.descriptor,
            (1.0,) + tuple(0.0 for _ in range(self.descriptor.dimension - 1)),
        )

    def encode_image(self, _image: Image.Image) -> VisualEmbedding:
        self.image_calls += 1
        return VisualEmbedding(
            self.descriptor,
            (1.0,) + tuple(0.0 for _ in range(self.descriptor.dimension - 1)),
        )


class FakeQueue:
    def __init__(self) -> None:
        self.submissions = 0
        self.priorities = []

    async def submit(self, operation, *, priority):
        self.submissions += 1
        self.priorities.append(priority)
        return operation()


def test_repeated_text_request_bypasses_inference_queue_after_cache_fill(
    monkeypatch,
) -> None:
    async def verify() -> None:
        encoder = FakeEncoder()
        queue = FakeQueue()
        monkeypatch.setenv("VISUAL_ENCODER_INTERNAL_KEY", "secret")
        main.encoder = encoder
        main.inference_queue = queue
        main.image_cache = BoundedEmbeddingCache(4)
        main.text_cache = BoundedEmbeddingCache(4)
        try:
            body = main.EncodeTextRequest(text="blue floral embroidery")
            first = await main.encode_text(body, authorization="Bearer secret")
            second = await main.encode_text(body, authorization="Bearer secret")
        finally:
            main.encoder = None
            main.inference_queue = None
            main.image_cache = None
            main.text_cache = None

        assert first == second
        assert encoder.text_calls == 1
        assert queue.submissions == 1
        assert queue.priorities == ["interactive"]

    asyncio.run(verify())


def test_repeated_image_request_bypasses_queue_without_full_rgb_hashing(
    monkeypatch,
) -> None:
    async def verify() -> None:
        encoder = FakeEncoder()
        queue = FakeQueue()
        monkeypatch.setenv("VISUAL_ENCODER_INTERNAL_KEY", "secret")
        main.encoder = encoder
        main.inference_queue = queue
        main.image_cache = BoundedEmbeddingCache(4)
        main.text_cache = BoundedEmbeddingCache(4)

        stream = BytesIO()
        Image.new("RGB", (512, 384), "navy").save(
            stream,
            format="JPEG",
            quality=95,
        )
        body = main.EncodeRequest(
            image_base64=b64encode(stream.getvalue()).decode("ascii")
        )
        try:
            first = await main.encode(body, authorization="Bearer secret")
            second = await main.encode(body, authorization="Bearer secret")
        finally:
            main.encoder = None
            main.inference_queue = None
            main.image_cache = None
            main.text_cache = None

        assert first == second
        assert encoder.image_calls == 1
        assert queue.submissions == 1
        assert queue.priorities == ["interactive"]
        assert main.image_cache is None

    asyncio.run(verify())


def test_binary_image_endpoint_preserves_priority_and_cache_contract(
    monkeypatch,
) -> None:
    encoder = FakeEncoder()
    queue = FakeQueue()
    monkeypatch.setenv("VISUAL_ENCODER_INTERNAL_KEY", "secret")
    main.encoder = encoder
    main.inference_queue = queue
    main.image_cache = BoundedEmbeddingCache(4)
    main.text_cache = BoundedEmbeddingCache(4)

    stream = BytesIO()
    Image.new("RGB", (64, 48), "navy").save(
        stream,
        format="JPEG",
        quality=95,
    )
    client = TestClient(main.app)
    try:
        response = client.post(
            "/v1/encode-image-bytes",
            params={"priority": "background"},
            content=stream.getvalue(),
            headers={
                "Authorization": "Bearer secret",
                "Content-Type": "image/jpeg",
            },
        )
    finally:
        client.close()
        main.encoder = None
        main.inference_queue = None
        main.image_cache = None
        main.text_cache = None

    assert response.status_code == 200
    assert encoder.image_calls == 1
    assert queue.submissions == 1
    assert queue.priorities == ["background"]
