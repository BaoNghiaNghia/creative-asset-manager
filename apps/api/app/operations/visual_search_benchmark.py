from __future__ import annotations

import argparse
import importlib
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.modules.visual_search.contracts import VisualEncoder
from app.modules.visual_search.preprocess import decode_visual_image


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Benchmark an explicitly supplied development visual encoder."
    )
    value.add_argument("--encoder-factory", required=True, help="module.path:callable")
    value.add_argument("--image", required=True, type=Path)
    value.add_argument("--iterations", type=int, default=20)
    return value


def load_encoder_factory(value: str) -> Callable[[], VisualEncoder]:
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--encoder-factory must use module.path:callable")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise ValueError("--encoder-factory target must be callable")
    return factory


def current_rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def percentile(samples: list[float], quantile: float) -> float:
    if not samples:
        raise ValueError("samples are required")
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def benchmark(
    factory: Callable[[], VisualEncoder],
    *,
    image_content: bytes,
    iterations: int,
) -> dict[str, Any]:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    started = time.perf_counter()
    encoder = factory()
    cold_load_ms = (time.perf_counter() - started) * 1_000
    prepared = decode_visual_image(image_content)
    single_started = time.perf_counter()
    single = encoder.encode_image(prepared.image)
    single_ms = (time.perf_counter() - single_started) * 1_000
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        encoded = encoder.encode_image(prepared.image)
        if encoded.descriptor != single.descriptor:
            raise ValueError("encoder descriptor changed during benchmark")
        samples.append((time.perf_counter() - started) * 1_000)
    return {
        "descriptor": {
            "encoder_name": single.descriptor.encoder_name,
            "encoder_revision": single.descriptor.encoder_revision,
            "embedding_schema_version": single.descriptor.embedding_schema_version,
            "dimension": single.descriptor.dimension,
            "preprocess_version": single.descriptor.preprocess_version,
            "similarity": single.descriptor.similarity,
        },
        "cold_load_ms": round(cold_load_ms, 3),
        "rss_after_load_bytes": current_rss_bytes(),
        "single_encode_ms": round(single_ms, 3),
        "sequential_count": iterations,
        "sequential_p50_ms": round(statistics.median(samples), 3),
        "sequential_p95_ms": round(percentile(samples, 0.95), 3),
        "rss_after_benchmark_bytes": current_rss_bytes(),
    }


def main() -> None:
    args = parser().parse_args()
    result = benchmark(
        load_encoder_factory(args.encoder_factory),
        image_content=args.image.read_bytes(),
        iterations=args.iterations,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
