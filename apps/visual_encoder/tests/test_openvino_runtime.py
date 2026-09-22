from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.modules.visual_search.model_spec import (
    SIGLIP2_REVISION,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
)
from siglip import (
    OPENVINO_ARTIFACT_FILES,
    OPENVINO_ARTIFACT_FORMAT,
    OPENVINO_BASELINE_VERSION,
    OpenVinoSiglip2Encoder,
    SiglipEncoderLoadError,
    _sha256_file,
)


class FakeProcessor:
    image_processor = SimpleNamespace(size={"height": 224, "width": 224})

    def __init__(self):
        self.tokenizer = self

    def __call__(self, *args, **kwargs):
        if args:
            assert args == (["blue embroidery"],)
            assert kwargs["max_length"] == 64
            assert kwargs["return_attention_mask"] is True
        if "images" in kwargs:
            return {"pixel_values": [[1.0]]}
        return {
            "input_ids": [[1, 2]],
            "attention_mask": [[1, 1]],
        }


class FakeInferRequest:
    def __init__(self, kind: str):
        self.kind = kind
        self.calls = 0

    def infer(self, inputs):
        self.calls += 1
        assert isinstance(inputs, dict)
        dimension = VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension
        if self.kind == "image":
            return {"embedding": [[1.0, *([0.0] * (dimension - 1))]]}
        return {"embedding": [[0.0, 1.0, *([0.0] * (dimension - 2))]]}


class FakeCompiled:
    def __init__(self, kind: str):
        self.kind = kind
        self.request = FakeInferRequest(kind)

    def create_infer_request(self):
        return self.request


class FakeCore:
    instances: list["FakeCore"] = []

    def __init__(self):
        self.compiles: list[tuple[str, str, dict[str, object]]] = []
        self.__class__.instances.append(self)

    def read_model(self, path: str):
        return Path(path).name

    def compile_model(self, model, device: str, config: dict[str, object]):
        self.compiles.append((model, device, dict(config)))
        return FakeCompiled("image" if str(model).startswith("image") else "text")


def _install_runtime_modules(monkeypatch) -> None:
    openvino = ModuleType("openvino")
    openvino.__version__ = OPENVINO_BASELINE_VERSION
    openvino.Core = FakeCore
    monkeypatch.setitem(sys.modules, "openvino", openvino)

    transformers = ModuleType("transformers")
    transformers.__version__ = "4.51.3"
    transformers.AutoProcessor = SimpleNamespace(
        from_pretrained=lambda *_args, **kwargs: (
            FakeProcessor()
            if kwargs.get("local_files_only") is True
            else (_ for _ in ()).throw(AssertionError("must load local processor"))
        )
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)


def _write_artifact(tmp_path: Path) -> tuple[Path, Path]:
    snapshot = tmp_path / SIGLIP2_REVISION
    snapshot.mkdir()
    artifact = tmp_path / "openvino"
    artifact.mkdir()
    for name in OPENVINO_ARTIFACT_FILES:
        (artifact / name).write_bytes(f"test-{name}".encode())

    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    manifest = {
        "artifact_format": OPENVINO_ARTIFACT_FORMAT,
        "precision": "fp32",
        "openvino_version": OPENVINO_BASELINE_VERSION,
        "transformers_version": "4.51.3",
        "encoder_name": descriptor.encoder_name,
        "encoder_revision": descriptor.encoder_revision,
        "embedding_schema_version": descriptor.embedding_schema_version,
        "dimension": descriptor.dimension,
        "preprocess_version": descriptor.preprocess_version,
        "similarity": descriptor.similarity,
        "files": {
            name: _sha256_file(artifact / name)
            for name in OPENVINO_ARTIFACT_FILES
        },
    }
    (artifact / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return snapshot, artifact


def test_openvino_runtime_loads_pinned_artifact_and_encodes_both_towers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    FakeCore.instances.clear()
    _install_runtime_modules(monkeypatch)
    snapshot, artifact = _write_artifact(tmp_path)

    encoder = OpenVinoSiglip2Encoder(
        snapshot,
        artifact,
        inference_threads=2,
        streams=1,
    )

    image = encoder.encode_image(Image.new("RGB", (224, 224)))
    text = encoder.encode_text("blue embroidery")

    assert image.descriptor == VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    assert text.descriptor == VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    assert image.values[0] == pytest.approx(1.0)
    assert text.values[1] == pytest.approx(1.0)
    assert encoder.runtime_info() == {
        "runtime": "openvino",
        "openvino_inference_threads": 2,
        "openvino_streams": 1,
    }
    assert len(FakeCore.instances) == 1
    assert encoder._image_request.calls == 1
    assert encoder._text_request.calls == 1
    assert FakeCore.instances[0].compiles == [
        (
            "image_encoder.xml",
            "CPU",
            {
                "INFERENCE_NUM_THREADS": 2,
                "NUM_STREAMS": 1,
            },
        ),
        (
            "text_encoder.xml",
            "CPU",
            {
                "INFERENCE_NUM_THREADS": 2,
                "NUM_STREAMS": 1,
            },
        ),
    ]


def test_openvino_runtime_rejects_tampered_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _install_runtime_modules(monkeypatch)
    snapshot, artifact = _write_artifact(tmp_path)
    (artifact / "image_encoder.bin").write_bytes(b"tampered")

    with pytest.raises(SiglipEncoderLoadError, match="hash mismatch"):
        OpenVinoSiglip2Encoder(snapshot, artifact)
