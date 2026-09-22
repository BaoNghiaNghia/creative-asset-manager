from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import statistics
from io import BytesIO
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from PIL import Image


def _host_memory_snapshot(
    path: Path = Path("/proc/meminfo"),
) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            name, raw = line.split(":", 1)
            parts = raw.strip().split()
            if not parts:
                continue
            value = int(parts[0])
            if len(parts) > 1 and parts[1].casefold() == "kb":
                value *= 1024
            values[name] = value
    except (OSError, ValueError) as exc:
        raise RuntimeError("host memory information is unavailable") from exc
    swap_total = int(values.get("SwapTotal", 0))
    swap_free = int(values.get("SwapFree", 0))
    return {
        "memory_available_bytes": int(values.get("MemAvailable", 0)),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _jpeg_payloads(path: Path, count: int) -> list[str]:
    if count <= 0:
        raise ValueError("count must be positive")
    try:
        with Image.open(path) as opened:
            source = opened.convert("RGB")
    except OSError as exc:
        raise RuntimeError("benchmark image is unavailable") from exc

    payloads: list[str] = []
    for index in range(count):
        image = source.copy()
        patch_width = min(16, image.width)
        patch_height = min(16, image.height)
        color = (
            (17 * (index + 1)) % 256,
            (97 * (index + 1)) % 256,
            (193 * (index + 1)) % 256,
        )
        image.paste(color, (0, 0, patch_width, patch_height))
        stream = BytesIO()
        image.save(
            stream,
            format="JPEG",
            quality=95,
            optimize=True,
            subsampling=0,
        )
        payloads.append(base64.b64encode(stream.getvalue()).decode("ascii"))
    return payloads


async def benchmark_encoder_load(
    *,
    encoder_url: str,
    internal_key: str,
    request_count: int,
    concurrency: int,
    timeout_seconds: float,
    image_path: Path | None = None,
    text_prefix: str = "visual search benchmark",
    max_swap_growth_bytes: int | None = None,
) -> dict[str, Any]:
    if not internal_key.strip():
        raise ValueError("internal_key must be configured")
    if request_count <= 0:
        raise ValueError("request_count must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_swap_growth_bytes is not None and max_swap_growth_bytes < 0:
        raise ValueError("max_swap_growth_bytes must be non-negative")

    base_url = encoder_url.rstrip("/")
    headers = {"Authorization": f"Bearer {internal_key}"}
    if image_path is not None:
        payloads = [
            {"image_base64": value, "priority": "interactive"}
            for value in _jpeg_payloads(image_path, request_count)
        ]
        endpoint = "/v1/encode-image"
        mode = "image"
    else:
        payloads = [
            {
                "text": f"{text_prefix} {index + 1}",
                "priority": "interactive",
            }
            for index in range(request_count)
        ]
        endpoint = "/v1/encode-text"
        mode = "text"

    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    status_counts: dict[str, int] = {}
    queue_depth_samples: list[int] = []
    queue_wait_p95_samples: list[float] = []
    memory_samples: list[dict[str, int]] = []
    memory_before = _host_memory_snapshot()
    memory_samples.append(memory_before)

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=timeout_seconds,
    ) as client:
        before = (await client.get("/ready")).json()

        stop_polling = asyncio.Event()

        async def poll_ready() -> None:
            while not stop_polling.is_set():
                try:
                    payload = (await client.get("/ready")).json()
                    queue = payload.get("queue") if isinstance(payload, dict) else None
                    if isinstance(queue, dict):
                        queued = queue.get("queued")
                        if isinstance(queued, int):
                            queue_depth_samples.append(queued)
                        wait = queue.get("queue_wait_ms")
                        if isinstance(wait, dict):
                            p95 = wait.get("p95")
                            if isinstance(p95, (int, float)):
                                queue_wait_p95_samples.append(float(p95))
                except (httpx.HTTPError, ValueError):
                    pass
                try:
                    memory_samples.append(_host_memory_snapshot())
                except RuntimeError:
                    pass
                await asyncio.sleep(0.05)

        poller = asyncio.create_task(poll_ready())

        async def send(payload: dict[str, object]) -> None:
            async with semaphore:
                started = perf_counter()
                try:
                    response = await client.post(endpoint, json=payload)
                    status_key = str(response.status_code)
                    if response.status_code == 503:
                        try:
                            detail = response.json().get("detail")
                            if isinstance(detail, dict) and detail.get("code"):
                                status_key = str(detail["code"])
                        except (TypeError, ValueError):
                            pass
                except httpx.HTTPError:
                    status_key = "transport_error"
                latencies.append((perf_counter() - started) * 1000.0)
                status_counts[status_key] = status_counts.get(status_key, 0) + 1

        started = perf_counter()
        await asyncio.gather(*(send(payload) for payload in payloads))
        elapsed_seconds = perf_counter() - started
        stop_polling.set()
        await poller
        after = (await client.get("/ready")).json()

    memory_after = _host_memory_snapshot()
    memory_samples.append(memory_after)
    swap_before = memory_before["swap_used_bytes"]
    swap_max = max(
        (sample["swap_used_bytes"] for sample in memory_samples),
        default=swap_before,
    )
    swap_growth = max(0, swap_max - swap_before)
    min_memory_available = min(
        (
            sample["memory_available_bytes"]
            for sample in memory_samples
        ),
        default=memory_before["memory_available_bytes"],
    )
    swap_passed = (
        None
        if max_swap_growth_bytes is None
        else swap_growth <= max_swap_growth_bytes
    )

    queue_after = after.get("queue") if isinstance(after, dict) else {}
    cache_after = after.get("cache") if isinstance(after, dict) else {}
    return {
        "mode": mode,
        "request_count": request_count,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed_seconds,
        "queries_per_second": (
            request_count / elapsed_seconds
            if elapsed_seconds > 0
            else 0.0
        ),
        "request_latency_ms": {
            "p50": statistics.median(latencies),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies),
        },
        "status_counts": status_counts,
        "queue": {
            "max_observed_depth": max(queue_depth_samples, default=0),
            "max_observed_wait_p95_ms": max(
                queue_wait_p95_samples,
                default=0.0,
            ),
            "before": (
                before.get("queue")
                if isinstance(before, dict)
                else None
            ),
            "after": queue_after,
        },
        "cache_after": cache_after,
        "host_memory": {
            "before": memory_before,
            "after": memory_after,
            "minimum_available_bytes": min_memory_available,
        },
        "swap_pressure": {
            "before_used_bytes": swap_before,
            "max_observed_used_bytes": swap_max,
            "after_used_bytes": memory_after["swap_used_bytes"],
            "growth_bytes": swap_growth,
            "max_allowed_growth_bytes": max_swap_growth_bytes,
            "passed": swap_passed,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded load probe against the isolated visual encoder. "
            "The report never includes raw image bytes or raw query payloads."
        )
    )
    parser.add_argument(
        "--encoder-url",
        default="http://127.0.0.1:8091",
    )
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--image", type=Path)
    parser.add_argument(
        "--text-prefix",
        default="visual search benchmark",
    )
    parser.add_argument(
        "--max-swap-growth-mib",
        type=float,
        help=(
            "Reviewed maximum allowed swap growth during this bounded probe. "
            "When omitted, swap pressure is measured but not auto-passed."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if (
        args.max_swap_growth_mib is not None
        and args.max_swap_growth_mib < 0
    ):
        parser.error("--max-swap-growth-mib must be non-negative")
    max_swap_growth_bytes = (
        None
        if args.max_swap_growth_mib is None
        else int(args.max_swap_growth_mib * 1024 * 1024)
    )

    internal_key = os.environ.get("VISUAL_ENCODER_INTERNAL_KEY", "")
    report = asyncio.run(
        benchmark_encoder_load(
            encoder_url=args.encoder_url,
            internal_key=internal_key,
            request_count=args.requests,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout_seconds,
            image_path=args.image,
            text_prefix=args.text_prefix,
            max_swap_growth_bytes=max_swap_growth_bytes,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
