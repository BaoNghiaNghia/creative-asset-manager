from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from PIL import Image


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.model_spec import VISUAL_SEARCH_ACTIVE_DESCRIPTOR
import validate_int8


def _vector(first: float) -> tuple[float, ...]:
    second = math.sqrt(max(0.0, 1.0 - first * first))
    return (first, second) + tuple(
        0.0 for _ in range(VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension - 2)
    )


def _embedding(first: float) -> VisualEmbedding:
    return VisualEmbedding(VISUAL_SEARCH_ACTIVE_DESCRIPTOR, _vector(first))


class FakeReference:
    def __init__(self, *_args, **_kwargs):
        pass

    def encode_image(self, image: Image.Image):
        pixel = image.getpixel((0, 0))
        if pixel[0] > 200:
            return _embedding(1.0)
        if pixel[1] > 200:
            return _embedding(0.90)
        return _embedding(0.10)

    def encode_text(self, _text: str):
        return _embedding(1.0)


class FakeCandidate:
    def __init__(self, *_args, **_kwargs):
        pass

    def encode_image(self, path: Path):
        name = Path(path).stem
        if name == "query":
            return _embedding(0.9999)
        if name == "positive":
            return _embedding(0.895)
        return _embedding(0.105)

    def encode_text(self, _text: str):
        return _embedding(0.9999)


def test_int8_validation_preserves_representative_rankings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(validate_int8, "OpenVinoSiglip2Encoder", FakeReference)
    monkeypatch.setattr(validate_int8, "Int8CandidateEncoder", FakeCandidate)

    Image.new("RGB", (4, 4), (255, 0, 0)).save(tmp_path / "query.png")
    Image.new("RGB", (4, 4), (0, 255, 0)).save(tmp_path / "positive.png")
    Image.new("RGB", (4, 4), (0, 0, 255)).save(tmp_path / "negative.png")
    sanity = tmp_path / "sanity.json"
    sanity.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "same-product",
                        "kind": "image_triplet",
                        "query": "query.png",
                        "positive": "positive.png",
                        "negative": "negative.png",
                    },
                    {
                        "id": "text-refinement",
                        "kind": "text_image",
                        "text": "green garment",
                        "positive": "positive.png",
                        "negative": "negative.png",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = validate_int8.validate_int8(
        model_path=tmp_path / "model",
        fp32_artifact_path=tmp_path / "fp32",
        int8_artifact_path=tmp_path / "int8",
        sanity_set_path=sanity,
        minimum_cases=2,
        minimum_embedding_cosine=0.99,
    )

    assert report["passed"] is True
    assert report["case_count"] == 2
    assert report["ranking_preserved_cases"] == 2
    assert report["minimum_embedding_cosine_observed"] >= 0.99
