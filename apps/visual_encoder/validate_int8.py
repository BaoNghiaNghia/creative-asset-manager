from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from PIL import Image

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.visual_search.model_spec import (
    SIGLIP2_REVISION,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
)
from quantize_openvino import (
    INT8_ARTIFACT_FORMAT,
    NNCF_BASELINE_VERSION,
)
from siglip import (
    OPENVINO_ARTIFACT_FILES,
    OPENVINO_BASELINE_VERSION,
    OpenVinoSiglip2Encoder,
    SiglipEncoderLoadError,
    _normalized_embedding,
    _sha256_file,
)


_TRANSFORMERS_VERSION = "4.51.3"


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class Int8CandidateEncoder:
    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR

    def __init__(self, model_path: Path, artifact_path: Path) -> None:
        if not model_path.is_dir() or model_path.name != SIGLIP2_REVISION:
            raise RuntimeError("model_path must be the exact pinned SigLIP2 snapshot")
        try:
            manifest = json.loads(
                (artifact_path / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("INT8 candidate manifest is unavailable or invalid") from exc

        descriptor = self.descriptor
        expected = {
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
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise RuntimeError("INT8 candidate does not match the active descriptor")
        hashes = manifest.get("files")
        if not isinstance(hashes, dict):
            raise RuntimeError("INT8 candidate hashes are missing")
        for name in OPENVINO_ARTIFACT_FILES:
            path = artifact_path / name
            if not path.is_file() or hashes.get(name) != _sha256_file(path):
                raise RuntimeError("INT8 candidate hash mismatch")

        import openvino as ov
        import transformers
        from transformers import AutoProcessor

        if not str(getattr(ov, "__version__", "")).startswith(
            OPENVINO_BASELINE_VERSION
        ):
            raise RuntimeError("OpenVINO validation runtime version mismatch")
        if getattr(transformers, "__version__", "") != _TRANSFORMERS_VERSION:
            raise RuntimeError("Transformers validation runtime version mismatch")

        self._processor = AutoProcessor.from_pretrained(
            model_path,
            local_files_only=True,
        )
        core = ov.Core()
        config = {
            "PERFORMANCE_HINT": "LATENCY",
            "INFERENCE_NUM_THREADS": 2,
            "NUM_STREAMS": 1,
        }
        self._image = core.compile_model(
            core.read_model(str(artifact_path / "image_encoder.xml")),
            "CPU",
            config,
        )
        self._text = core.compile_model(
            core.read_model(str(artifact_path / "text_encoder.xml")),
            "CPU",
            config,
        )

    @staticmethod
    def _first_output(result: Any) -> Any:
        try:
            return next(iter(result.values()))
        except (AttributeError, StopIteration) as exc:
            raise RuntimeError("INT8 candidate returned no embedding output") from exc

    def encode_image(self, path: Path):
        with Image.open(path) as opened:
            image = opened.convert("RGB")
            inputs = self._processor(images=image, return_tensors="np")
        result = self._image({"pixel_values": inputs["pixel_values"]})
        return _normalized_embedding(self._first_output(result))

    def encode_text(self, text: str):
        value = text.strip()
        if not value:
            raise ValueError("text must be non-empty")
        inputs = self._processor(
            text=[value],
            padding="max_length",
            truncation=True,
            return_tensors="np",
        )
        result = self._text(
            {
                "input_ids": inputs["input_ids"],
                "attention_mask": inputs["attention_mask"],
            }
        )
        return _normalized_embedding(self._first_output(result))


def _load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("INT8 sanity set is unavailable or invalid") from exc
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise RuntimeError("INT8 sanity set requires a non-empty cases array")
    return cases


def _resolve(base: Path, value: object) -> Path:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        raise RuntimeError(f"sanity-set asset is unavailable: {path}")
    return path


def _timed(callable_):
    started = perf_counter()
    value = callable_()
    return value, (perf_counter() - started) * 1000.0


def validate_int8(
    *,
    model_path: Path,
    fp32_artifact_path: Path,
    int8_artifact_path: Path,
    sanity_set_path: Path,
    minimum_cases: int = 50,
    minimum_embedding_cosine: float = 0.995,
) -> dict[str, Any]:
    if minimum_cases <= 0:
        raise ValueError("minimum_cases must be positive")
    if not 0.0 < minimum_embedding_cosine <= 1.0:
        raise ValueError("minimum_embedding_cosine must be in (0, 1]")

    cases = _load_cases(sanity_set_path)
    if len(cases) < minimum_cases:
        raise RuntimeError(
            f"INT8 sanity set has {len(cases)} cases; at least {minimum_cases} are required"
        )

    try:
        reference = OpenVinoSiglip2Encoder(
            model_path,
            fp32_artifact_path,
            inference_threads=2,
            streams=1,
        )
    except SiglipEncoderLoadError as exc:
        raise RuntimeError("validated FP32 OpenVINO baseline is unavailable") from exc
    candidate = Int8CandidateEncoder(model_path, int8_artifact_path)

    base = sanity_set_path.parent
    rows: list[dict[str, Any]] = []
    candidate_latencies: list[float] = []
    reference_latencies: list[float] = []

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise RuntimeError(f"sanity case {index} must be an object")
        kind = str(case.get("kind") or "")
        case_id = str(case.get("id") or f"case-{index + 1}")

        if kind == "image_triplet":
            query = _resolve(base, case.get("query"))
            positive = _resolve(base, case.get("positive"))
            negative = _resolve(base, case.get("negative"))

            ref_query, ref_ms = _timed(
                lambda: reference.encode_image(Image.open(query).convert("RGB"))
            )
            int8_query, int8_ms = _timed(lambda: candidate.encode_image(query))
            ref_positive = reference.encode_image(
                Image.open(positive).convert("RGB")
            )
            ref_negative = reference.encode_image(
                Image.open(negative).convert("RGB")
            )
            int8_positive = candidate.encode_image(positive)
            int8_negative = candidate.encode_image(negative)
            reference_latencies.append(ref_ms)
            candidate_latencies.append(int8_ms)

            compatibility = min(
                _cosine(ref_query.values, int8_query.values),
                _cosine(ref_positive.values, int8_positive.values),
                _cosine(ref_negative.values, int8_negative.values),
            )
            reference_margin = (
                _cosine(ref_query.values, ref_positive.values)
                - _cosine(ref_query.values, ref_negative.values)
            )
            candidate_margin = (
                _cosine(int8_query.values, int8_positive.values)
                - _cosine(int8_query.values, int8_negative.values)
            )
        elif kind == "text_image":
            text = str(case.get("text") or "").strip()
            if not text:
                raise RuntimeError(f"sanity case {case_id} requires text")
            positive = _resolve(base, case.get("positive"))
            negative = _resolve(base, case.get("negative"))

            ref_query, ref_ms = _timed(lambda: reference.encode_text(text))
            int8_query, int8_ms = _timed(lambda: candidate.encode_text(text))
            ref_positive = reference.encode_image(
                Image.open(positive).convert("RGB")
            )
            ref_negative = reference.encode_image(
                Image.open(negative).convert("RGB")
            )
            int8_positive = candidate.encode_image(positive)
            int8_negative = candidate.encode_image(negative)
            reference_latencies.append(ref_ms)
            candidate_latencies.append(int8_ms)

            compatibility = min(
                _cosine(ref_query.values, int8_query.values),
                _cosine(ref_positive.values, int8_positive.values),
                _cosine(ref_negative.values, int8_negative.values),
            )
            reference_margin = (
                _cosine(ref_query.values, ref_positive.values)
                - _cosine(ref_query.values, ref_negative.values)
            )
            candidate_margin = (
                _cosine(int8_query.values, int8_positive.values)
                - _cosine(int8_query.values, int8_negative.values)
            )
        else:
            raise RuntimeError(
                f"sanity case {case_id} has unsupported kind {kind!r}"
            )

        passed = (
            compatibility >= minimum_embedding_cosine
            and reference_margin > 0.0
            and candidate_margin > 0.0
        )
        rows.append(
            {
                "id": case_id,
                "kind": kind,
                "embedding_cosine_min": compatibility,
                "reference_margin": reference_margin,
                "candidate_margin": candidate_margin,
                "ranking_preserved": candidate_margin > 0.0,
                "passed": passed,
            }
        )

    minimum_observed = min(float(row["embedding_cosine_min"]) for row in rows)
    report = {
        "candidate_status": "candidate_unvalidated",
        "embedding_schema_version": VISUAL_SEARCH_ACTIVE_DESCRIPTOR.embedding_schema_version,
        "minimum_cases_required": minimum_cases,
        "case_count": len(rows),
        "minimum_embedding_cosine_required": minimum_embedding_cosine,
        "minimum_embedding_cosine_observed": minimum_observed,
        "ranking_preserved_cases": sum(
            1 for row in rows if row["ranking_preserved"]
        ),
        "reference_query_latency_ms": {
            "median": statistics.median(reference_latencies),
            "max": max(reference_latencies),
        },
        "int8_query_latency_ms": {
            "median": statistics.median(candidate_latencies),
            "max": max(candidate_latencies),
        },
        "cases": rows,
        "passed": all(bool(row["passed"]) for row in rows),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an INT8 SigLIP2 candidate against the approved FP32 "
            "OpenVINO baseline using representative ranking sanity cases."
        )
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--fp32-artifact-path", type=Path, required=True)
    parser.add_argument("--int8-artifact-path", type=Path, required=True)
    parser.add_argument("--sanity-set", type=Path, required=True)
    parser.add_argument("--minimum-cases", type=int, default=50)
    parser.add_argument("--minimum-embedding-cosine", type=float, default=0.995)
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()

    report = validate_int8(
        model_path=args.model_path,
        fp32_artifact_path=args.fp32_artifact_path,
        int8_artifact_path=args.int8_artifact_path,
        sanity_set_path=args.sanity_set,
        minimum_cases=args.minimum_cases,
        minimum_embedding_cosine=args.minimum_embedding_cosine,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
