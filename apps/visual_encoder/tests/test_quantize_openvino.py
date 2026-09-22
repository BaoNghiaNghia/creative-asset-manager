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
from quantize_openvino import (
    INT8_ARTIFACT_FORMAT,
    NNCF_BASELINE_VERSION,
    quantize_openvino,
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
    def __call__(self, **kwargs):
        if "images" in kwargs:
            return {"pixel_values": [[1.0]]}
        return {
            "input_ids": [[1, 2]],
            "attention_mask": [[1, 1]],
        }


class FakeCore:
    def read_model(self, path: str):
        return Path(path).name


class FakeDataset:
    def __init__(self, data, transform):
        self.data = list(data)
        self.transform = transform


def _write_fp32_artifact(root: Path) -> Path:
    root.mkdir()
    for name in OPENVINO_ARTIFACT_FILES:
        (root / name).write_bytes(f"fp32-{name}".encode())
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
            name: _sha256_file(root / name)
            for name in OPENVINO_ARTIFACT_FILES
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _install_fake_modules(monkeypatch):
    calls = []

    nncf = ModuleType("nncf")
    nncf.__version__ = NNCF_BASELINE_VERSION
    nncf.Dataset = FakeDataset
    nncf.QuantizationPreset = SimpleNamespace(MIXED="mixed")
    nncf.ModelType = SimpleNamespace(TRANSFORMER="transformer")
    nncf.TargetDevice = SimpleNamespace(CPU="cpu")

    def quantize(model, dataset, **kwargs):
        assert isinstance(dataset, FakeDataset)
        calls.append((model, dataset, kwargs))
        return f"int8-{model}"

    nncf.quantize = quantize
    monkeypatch.setitem(sys.modules, "nncf", nncf)

    openvino = ModuleType("openvino")
    openvino.__version__ = OPENVINO_BASELINE_VERSION
    openvino.Core = FakeCore

    def save_model(model, path, *, compress_to_fp16):
        assert compress_to_fp16 is False
        xml = Path(path)
        xml.write_bytes(str(model).encode())
        xml.with_suffix(".bin").write_bytes(b"int8-bin")

    openvino.save_model = save_model
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
    return calls


def test_quantize_openvino_builds_non_promoted_int8_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls = _install_fake_modules(monkeypatch)
    snapshot = tmp_path / SIGLIP2_REVISION
    snapshot.mkdir()
    fp32 = _write_fp32_artifact(tmp_path / "fp32")

    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (16, 16), "white").save(images / "a.png")
    Image.new("RGB", (16, 16), "black").save(images / "b.png")
    text = tmp_path / "text.txt"
    text.write_text("white garment\nblack garment\n", encoding="utf-8")
    output = tmp_path / "int8"

    result = quantize_openvino(
        model_path=snapshot,
        fp32_artifact_path=fp32,
        output_path=output,
        image_calibration_dir=images,
        text_calibration_file=text,
        subset_size=300,
    )

    assert result == output
    assert len(calls) == 2
    for _model, dataset, kwargs in calls:
        assert len(dataset.data) == 2
        assert kwargs["subset_size"] == 2
        assert kwargs["preset"] == "mixed"
        assert kwargs["model_type"] == "transformer"
        assert kwargs["target_device"] == "cpu"
        assert kwargs["fast_bias_correction"] is False

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["artifact_format"] == INT8_ARTIFACT_FORMAT
    assert manifest["precision"] == "int8"
    assert manifest["compatibility_status"] == "candidate_unvalidated"
    assert manifest["nncf_version"] == NNCF_BASELINE_VERSION
    assert manifest["calibration"]["image_samples"] == 2
    assert manifest["calibration"]["text_samples"] == 2
    assert set(manifest["files"]) == set(OPENVINO_ARTIFACT_FILES)

    with pytest.raises(
        SiglipEncoderLoadError,
        match="does not match the active embedding contract",
    ):
        OpenVinoSiglip2Encoder(snapshot, output)
