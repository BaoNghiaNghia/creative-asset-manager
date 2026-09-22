from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.visual_search.model_spec import (
    SIGLIP2_REVISION,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
)
from siglip import (
    OPENVINO_ARTIFACT_FILES,
    OPENVINO_ARTIFACT_FORMAT,
    OPENVINO_BASELINE_VERSION,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _set_io_name(port, name: str) -> None:
    port.get_tensor().set_names({name})


def export_openvino(model_path: Path, output_path: Path, *, force: bool = False) -> Path:
    if not model_path.is_dir() or model_path.name != SIGLIP2_REVISION:
        raise RuntimeError("model_path must be the exact pinned SigLIP2 snapshot")

    if output_path.exists() and not force:
        existing = [name for name in (*OPENVINO_ARTIFACT_FILES, "manifest.json") if (output_path / name).exists()]
        if existing:
            raise RuntimeError(
                "OpenVINO output already contains an artifact; pass --force to replace known files"
            )
    output_path.mkdir(parents=True, exist_ok=True)

    if force:
        for name in (*OPENVINO_ARTIFACT_FILES, "manifest.json"):
            path = output_path / name
            if path.is_file():
                path.unlink()

    import openvino as ov
    import torch
    import transformers
    from transformers import AutoModel, AutoProcessor

    if not str(getattr(ov, "__version__", "")).startswith(OPENVINO_BASELINE_VERSION):
        raise RuntimeError(
            f"OpenVINO {OPENVINO_BASELINE_VERSION} is required for reproducible export"
        )
    if getattr(transformers, "__version__", "") != "4.51.3":
        raise RuntimeError("Transformers 4.51.3 is required for reproducible export")

    model = AutoModel.from_pretrained(model_path, local_files_only=True).eval()
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)

    class ImageTower(torch.nn.Module):
        def __init__(self, delegate):
            super().__init__()
            self.delegate = delegate

        def forward(self, pixel_values):
            return torch.nn.functional.normalize(
                self.delegate.get_image_features(pixel_values=pixel_values),
                dim=-1,
            )

    class TextTower(torch.nn.Module):
        def __init__(self, delegate):
            super().__init__()
            self.delegate = delegate

        def forward(self, input_ids, attention_mask):
            return torch.nn.functional.normalize(
                self.delegate.get_text_features(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                ),
                dim=-1,
            )

    image_inputs = processor(
        images=Image.new("RGB", (224, 224), (127, 127, 127)),
        return_tensors="pt",
    )
    text_inputs = processor(
        text=["visual search"],
        padding="max_length",
        truncation=True,
        return_tensors="pt",
    )

    with torch.inference_mode():
        image_ir = ov.convert_model(
            ImageTower(model).eval(),
            example_input=(image_inputs["pixel_values"],),
        )
        text_ir = ov.convert_model(
            TextTower(model).eval(),
            example_input=(
                text_inputs["input_ids"],
                text_inputs["attention_mask"],
            ),
        )

    _set_io_name(image_ir.inputs[0], "pixel_values")
    _set_io_name(image_ir.outputs[0], "embedding")
    _set_io_name(text_ir.inputs[0], "input_ids")
    _set_io_name(text_ir.inputs[1], "attention_mask")
    _set_io_name(text_ir.outputs[0], "embedding")

    ov.save_model(
        image_ir,
        output_path / "image_encoder.xml",
        compress_to_fp16=False,
    )
    ov.save_model(
        text_ir,
        output_path / "text_encoder.xml",
        compress_to_fp16=False,
    )

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
            name: _sha256_file(output_path / name)
            for name in OPENVINO_ARTIFACT_FILES
        },
    }
    (output_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the pinned CAM SigLIP2 snapshot to FP32 OpenVINO IR."
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        required=True,
        help="Exact pinned SigLIP2 snapshot directory.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        required=True,
        help="Destination directory for image/text tower IR files and manifest.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace only the known OpenVINO artifact files in the destination.",
    )
    args = parser.parse_args()
    print(export_openvino(args.model_path, args.output_path, force=args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
