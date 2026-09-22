from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageDraw

API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from siglip import OpenVinoSiglip2Encoder, SiglipVisualEncoder


_TEXT_CASES = (
    "embroidered baby bodysuit",
    "blue floral embroidery",
    "white garment on neutral background",
)


def _synthetic_image() -> Image.Image:
    image = Image.new("RGB", (224, 224), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((32, 40, 192, 184), fill=(35, 96, 160))
    draw.ellipse((72, 72, 152, 152), fill=(180, 120, 70))
    return image


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _timed(callable_, iterations: int) -> tuple[object, list[float]]:
    result = None
    latencies = []
    for _ in range(iterations):
        started = perf_counter()
        result = callable_()
        latencies.append((perf_counter() - started) * 1000.0)
    return result, latencies


def validate(
    model_path: Path,
    artifact_path: Path,
    *,
    iterations: int = 3,
    minimum_cosine: float = 0.999,
) -> dict[str, object]:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    reference = SiglipVisualEncoder(model_path)
    candidate = OpenVinoSiglip2Encoder(
        model_path,
        artifact_path,
        inference_threads=2,
        streams=1,
    )

    image = _synthetic_image()
    reference.encode_image(image)
    candidate.encode_image(image)

    ref_image, ref_image_ms = _timed(
        lambda: reference.encode_image(image),
        iterations,
    )
    ov_image, ov_image_ms = _timed(
        lambda: candidate.encode_image(image),
        iterations,
    )
    image_cosine = _cosine(ref_image.values, ov_image.values)

    text_rows = []
    for text in _TEXT_CASES:
        ref_text = reference.encode_text(text)
        ov_text = candidate.encode_text(text)
        text_rows.append(
            {
                "case": text,
                "cosine": _cosine(ref_text.values, ov_text.values),
            }
        )

    minimum_observed = min(
        [image_cosine, *(float(row["cosine"]) for row in text_rows)]
    )
    report = {
        "descriptor": candidate.descriptor.embedding_schema_version,
        "minimum_required_cosine": minimum_cosine,
        "minimum_observed_cosine": minimum_observed,
        "image_cosine": image_cosine,
        "text": text_rows,
        "reference_image_ms": {
            "median": statistics.median(ref_image_ms),
            "max": max(ref_image_ms),
        },
        "openvino_image_ms": {
            "median": statistics.median(ov_image_ms),
            "max": max(ov_image_ms),
        },
        "openvino_runtime": candidate.runtime_info(),
        "passed": minimum_observed >= minimum_cosine,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare FP32 OpenVINO SigLIP2 embeddings against the pinned PyTorch reference."
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--artifact-path", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--minimum-cosine", type=float, default=0.999)
    args = parser.parse_args()
    report = validate(
        args.model_path,
        args.artifact_path,
        iterations=args.iterations,
        minimum_cosine=args.minimum_cosine,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
