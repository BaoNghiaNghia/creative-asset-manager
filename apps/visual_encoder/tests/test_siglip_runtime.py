from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import main
from app.modules.visual_search.model_spec import SIGLIP2_REVISION, VISUAL_SEARCH_ACTIVE_DESCRIPTOR
from siglip import SiglipEncoderLoadError, SiglipVisualEncoder, build_visual_encoder


def test_bootstrap_uses_runtime_factory_and_matches_api_descriptor() -> None:
    assert main.build_visual_encoder is build_visual_encoder
    assert SiglipVisualEncoder.__module__ == "siglip"
    assert SiglipVisualEncoder.descriptor == VISUAL_SEARCH_ACTIVE_DESCRIPTOR


def test_siglip_rejects_any_non_pinned_snapshot_path(tmp_path: Path) -> None:
    with pytest.raises(SiglipEncoderLoadError):
        SiglipVisualEncoder(tmp_path)


def test_siglip2_image_and_text_embeddings_use_v2_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeTensor:
        def squeeze(self, _dim: int):
            return self

        def tolist(self):
            return [1.0] * VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension

    class FakeModel:
        config = SimpleNamespace(
            text_config=SimpleNamespace(projection_size=VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension)
        )

        def eval(self):
            return self

        def get_image_features(self, **_inputs):
            return FakeTensor()

        def get_text_features(self, **_inputs):
            return FakeTensor()

    class FakeProcessor:
        image_processor = SimpleNamespace(size={"height": 224, "width": 224})

        def __call__(self, **_kwargs):
            return {}

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(_path, *, local_files_only: bool):
            assert local_files_only is True
            return FakeModel()

    class FakeAutoProcessor:
        @staticmethod
        def from_pretrained(_path, *, local_files_only: bool):
            assert local_files_only is True
            return FakeProcessor()

    @contextmanager
    def inference_mode():
        yield

    fake_torch = SimpleNamespace(
        inference_mode=inference_mode,
        nn=SimpleNamespace(
            functional=SimpleNamespace(normalize=lambda tensor, dim: tensor)
        ),
    )
    fake_transformers = SimpleNamespace(
        AutoModel=FakeAutoModel,
        AutoProcessor=FakeAutoProcessor,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    snapshot = tmp_path / SIGLIP2_REVISION
    snapshot.mkdir()
    encoder = SiglipVisualEncoder(snapshot)

    image_embedding = encoder.encode_image(Image.new("RGB", (224, 224)))
    text_embedding = encoder.encode_text("embroidered baby bodysuit")

    for embedding in (image_embedding, text_embedding):
        assert embedding.descriptor == VISUAL_SEARCH_ACTIVE_DESCRIPTOR
        assert embedding.descriptor.embedding_schema_version == "visual_embedding_v2"
        assert len(embedding.values) == VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension
