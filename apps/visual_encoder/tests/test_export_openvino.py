from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace


API_ROOT = Path(__file__).resolve().parents[2] / "api"
ENCODER_ROOT = Path(__file__).resolve().parents[1]
for path in (str(API_ROOT), str(ENCODER_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.modules.visual_search.model_spec import (
    SIGLIP2_REVISION,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
)
from export_openvino import export_openvino
from siglip import OPENVINO_ARTIFACT_FILES, OPENVINO_ARTIFACT_FORMAT, OPENVINO_BASELINE_VERSION


class FakePort:
    def __init__(self):
        self.names = set()

    def get_tensor(self):
        return self

    def set_names(self, names):
        self.names = set(names)


class FakeIR:
    def __init__(self, input_count: int):
        self.inputs = [FakePort() for _ in range(input_count)]
        self.outputs = [FakePort()]


class FakeModule:
    def __init__(self, *args, **kwargs):
        pass

    def eval(self):
        return self


class FakeModel:
    def eval(self):
        return self


class FakeProcessor:
    def __init__(self):
        self.tokenizer = self

    def __call__(self, *args, **kwargs):
        if args:
            assert args == (["visual search"],)
            assert kwargs["max_length"] == 64
            assert kwargs["return_attention_mask"] is True
        if "images" in kwargs:
            return {"pixel_values": "pixels"}
        return {"input_ids": "ids", "attention_mask": "mask"}


@contextmanager
def _inference_mode():
    yield


def _install_fake_modules(monkeypatch):
    torch = ModuleType("torch")
    torch.nn = SimpleNamespace(
        Module=FakeModule,
        functional=SimpleNamespace(normalize=lambda value, dim: value),
    )
    torch.inference_mode = _inference_mode
    monkeypatch.setitem(sys.modules, "torch", torch)

    transformers = ModuleType("transformers")
    transformers.__version__ = "4.51.3"
    transformers.AutoModel = SimpleNamespace(
        from_pretrained=lambda *_args, **kwargs: FakeModel()
    )
    transformers.AutoProcessor = SimpleNamespace(
        from_pretrained=lambda *_args, **kwargs: FakeProcessor()
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)

    openvino = ModuleType("openvino")
    openvino.__version__ = OPENVINO_BASELINE_VERSION
    calls = []

    def convert_model(_model, *, example_input):
        ir = FakeIR(len(example_input))
        calls.append(ir)
        return ir

    def save_model(_ir, path, *, compress_to_fp16):
        assert compress_to_fp16 is False
        xml = Path(path)
        xml.write_bytes(b"xml")
        xml.with_suffix(".bin").write_bytes(b"bin")

    openvino.convert_model = convert_model
    openvino.save_model = save_model
    monkeypatch.setitem(sys.modules, "openvino", openvino)
    return calls


def test_export_openvino_writes_fp32_versioned_artifact(monkeypatch, tmp_path: Path) -> None:
    calls = _install_fake_modules(monkeypatch)
    snapshot = tmp_path / SIGLIP2_REVISION
    snapshot.mkdir()
    output = tmp_path / "ov"

    result = export_openvino(snapshot, output)

    assert result == output
    assert len(calls) == 2
    assert calls[0].inputs[0].names == {"pixel_values"}
    assert calls[1].inputs[0].names == {"input_ids"}
    assert calls[1].inputs[1].names == {"attention_mask"}
    for name in OPENVINO_ARTIFACT_FILES:
        assert (output / name).is_file()

    manifest = json.loads((output / "manifest.json").read_text())
    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    assert manifest["artifact_format"] == OPENVINO_ARTIFACT_FORMAT
    assert manifest["precision"] == "fp32"
    assert manifest["openvino_version"] == OPENVINO_BASELINE_VERSION
    assert manifest["embedding_schema_version"] == descriptor.embedding_schema_version
    assert manifest["encoder_revision"] == descriptor.encoder_revision
    assert set(manifest["files"]) == set(OPENVINO_ARTIFACT_FILES)
