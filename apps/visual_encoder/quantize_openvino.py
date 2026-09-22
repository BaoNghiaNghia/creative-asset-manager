from __future__ import annotations

import argparse
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
    _sha256_file,
)


INT8_ARTIFACT_FORMAT = "cam-siglip2-openvino-int8-candidate-v1"
NNCF_BASELINE_VERSION = "3.4.0"
_TRANSFORMERS_VERSION = "4.51.3"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _load_fp32_manifest(path: Path) -> dict[str, object]:
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("FP32 OpenVINO manifest is unavailable or invalid") from exc
    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    expected = {
        "artifact_format": OPENVINO_ARTIFACT_FORMAT,
        "precision": "fp32",
        "openvino_version": OPENVINO_BASELINE_VERSION,
        "transformers_version": _TRANSFORMERS_VERSION,
        "encoder_name": descriptor.encoder_name,
        "encoder_revision": descriptor.encoder_revision,
        "embedding_schema_version": descriptor.embedding_schema_version,
        "dimension": descriptor.dimension,
        "preprocess_version": descriptor.preprocess_version,
        "similarity": descriptor.similarity,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise RuntimeError("FP32 OpenVINO artifact does not match the active descriptor")
    hashes = manifest.get("files")
    if not isinstance(hashes, dict):
        raise RuntimeError("FP32 OpenVINO artifact hashes are missing")
    for name in OPENVINO_ARTIFACT_FILES:
        file_path = path / name
        if not file_path.is_file() or hashes.get(name) != _sha256_file(file_path):
            raise RuntimeError("FP32 OpenVINO artifact hash mismatch")
    return manifest


def _calibration_images(root: Path) -> list[Path]:
    if not root.is_dir():
        raise RuntimeError("image calibration directory is unavailable")
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES
    )
    if not paths:
        raise RuntimeError("image calibration set is empty")
    return paths


def _calibration_text(path: Path) -> list[str]:
    try:
        rows = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError as exc:
        raise RuntimeError("text calibration file is unavailable") from exc
    if not rows:
        raise RuntimeError("text calibration set is empty")
    if any(len(row) > 500 for row in rows):
        raise RuntimeError("text calibration entries must be at most 500 characters")
    return rows


def quantize_openvino(
    *,
    model_path: Path,
    fp32_artifact_path: Path,
    output_path: Path,
    image_calibration_dir: Path,
    text_calibration_file: Path,
    subset_size: int = 300,
    force: bool = False,
) -> Path:
    if subset_size <= 0:
        raise ValueError("subset_size must be positive")
    if not model_path.is_dir() or model_path.name != SIGLIP2_REVISION:
        raise RuntimeError("model_path must be the exact pinned SigLIP2 snapshot")

    source_manifest = _load_fp32_manifest(fp32_artifact_path)
    image_paths = _calibration_images(image_calibration_dir)
    text_rows = _calibration_text(text_calibration_file)

    if output_path.exists() and not force:
        existing = [
            name
            for name in (*OPENVINO_ARTIFACT_FILES, "manifest.json")
            if (output_path / name).exists()
        ]
        if existing:
            raise RuntimeError(
                "INT8 output already contains an artifact; pass --force to replace known files"
            )
    output_path.mkdir(parents=True, exist_ok=True)
    if force:
        for name in (*OPENVINO_ARTIFACT_FILES, "manifest.json"):
            path = output_path / name
            if path.is_file():
                path.unlink()

    import nncf
    import openvino as ov
    import transformers
    from transformers import AutoProcessor

    if not str(getattr(ov, "__version__", "")).startswith(OPENVINO_BASELINE_VERSION):
        raise RuntimeError(
            f"OpenVINO {OPENVINO_BASELINE_VERSION} is required for INT8 candidate build"
        )
    if getattr(nncf, "__version__", "") != NNCF_BASELINE_VERSION:
        raise RuntimeError(
            f"NNCF {NNCF_BASELINE_VERSION} is required for INT8 candidate build"
        )
    if getattr(transformers, "__version__", "") != _TRANSFORMERS_VERSION:
        raise RuntimeError(
            f"Transformers {_TRANSFORMERS_VERSION} is required for INT8 candidate build"
        )

    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    core = ov.Core()

    image_model = core.read_model(str(fp32_artifact_path / "image_encoder.xml"))
    text_model = core.read_model(str(fp32_artifact_path / "text_encoder.xml"))

    def image_transform(path: Path):
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            values = processor(images=image, return_tensors="np")
        return {"pixel_values": values["pixel_values"]}

    def text_transform(value: str):
        values = processor(
            text=[value],
            padding="max_length",
            truncation=True,
            return_tensors="np",
        )
        return {
            "input_ids": values["input_ids"],
            "attention_mask": values["attention_mask"],
        }

    image_dataset = nncf.Dataset(image_paths, image_transform)
    text_dataset = nncf.Dataset(text_rows, text_transform)
    common = {
        "preset": nncf.QuantizationPreset.MIXED,
        "model_type": nncf.ModelType.TRANSFORMER,
        "target_device": nncf.TargetDevice.CPU,
        "fast_bias_correction": False,
    }
    image_quantized = nncf.quantize(
        image_model,
        image_dataset,
        subset_size=min(subset_size, len(image_paths)),
        **common,
    )
    text_quantized = nncf.quantize(
        text_model,
        text_dataset,
        subset_size=min(subset_size, len(text_rows)),
        **common,
    )

    ov.save_model(
        image_quantized,
        output_path / "image_encoder.xml",
        compress_to_fp16=False,
    )
    ov.save_model(
        text_quantized,
        output_path / "text_encoder.xml",
        compress_to_fp16=False,
    )

    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    manifest = {
        "artifact_format": INT8_ARTIFACT_FORMAT,
        "precision": "int8",
        "compatibility_status": "candidate_unvalidated",
        "openvino_version": OPENVINO_BASELINE_VERSION,
        "nncf_version": NNCF_BASELINE_VERSION,
        "transformers_version": _TRANSFORMERS_VERSION,
        "encoder_name": descriptor.encoder_name,
        "encoder_revision": descriptor.encoder_revision,
        "embedding_schema_version": descriptor.embedding_schema_version,
        "dimension": descriptor.dimension,
        "preprocess_version": descriptor.preprocess_version,
        "similarity": descriptor.similarity,
        "calibration": {
            "image_samples": len(image_paths),
            "text_samples": len(text_rows),
            "requested_subset_size": subset_size,
            "image_subset_size": min(subset_size, len(image_paths)),
            "text_subset_size": min(subset_size, len(text_rows)),
            "preset": "MIXED",
            "model_type": "TRANSFORMER",
            "target_device": "CPU",
            "fast_bias_correction": False,
        },
        "source_fp32_manifest_sha256": _sha256_file(
            fp32_artifact_path / "manifest.json"
        ),
        "source_fp32_files": source_manifest["files"],
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
        description=(
            "Build a non-promoted INT8 SigLIP2 OpenVINO candidate from the "
            "validated FP32 artifact."
        )
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--fp32-artifact-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--image-calibration-dir", type=Path, required=True)
    parser.add_argument("--text-calibration-file", type=Path, required=True)
    parser.add_argument("--subset-size", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(
        quantize_openvino(
            model_path=args.model_path,
            fp32_artifact_path=args.fp32_artifact_path,
            output_path=args.output_path,
            image_calibration_dir=args.image_calibration_dir,
            text_calibration_file=args.text_calibration_file,
            subset_size=args.subset_size,
            force=args.force,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
