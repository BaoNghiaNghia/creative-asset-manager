from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"evidence report is unavailable or invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"evidence report must contain a JSON object: {path}")
    return payload


def _read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
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
    return values


def _host_snapshot(root_path: Path = Path("/")) -> dict[str, Any]:
    mem = _read_meminfo()
    disk = shutil.disk_usage(root_path)
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except OSError:
        load_1 = load_5 = load_15 = 0.0
    swap_total = int(mem.get("SwapTotal", 0))
    swap_free = int(mem.get("SwapFree", 0))
    return {
        "cpu_count": os.cpu_count(),
        "load_average": {
            "1m": load_1,
            "5m": load_5,
            "15m": load_15,
        },
        "memory_bytes": {
            "total": int(mem.get("MemTotal", 0)),
            "available": int(mem.get("MemAvailable", 0)),
        },
        "swap_bytes": {
            "total": swap_total,
            "used": max(0, swap_total - swap_free),
        },
        "root_disk_bytes": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        },
    }


def _process_rss_bytes(pid: int | None) -> int | None:
    if pid is None:
        return None
    if pid <= 0:
        raise ValueError("encoder_pid must be positive")
    status = Path(f"/proc/{pid}/status")
    try:
        lines = status.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError(f"encoder process {pid} is unavailable") from exc
    for line in lines:
        if line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1]) * 1024
    raise RuntimeError(f"encoder process {pid} RSS is unavailable")


def _get_json(
    client: httpx.Client,
    base_url: str,
    path: str,
) -> dict[str, Any]:
    response = client.get(base_url.rstrip("/") + path)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected JSON response for {path}")
    return payload


def _elasticsearch_snapshot(
    client: httpx.Client,
    base_url: str,
) -> dict[str, Any]:
    root = _get_json(client, base_url, "/")
    health = _get_json(client, base_url, "/_cluster/health")
    stats = _get_json(client, base_url, "/_cluster/stats")

    version = root.get("version")
    version_number = (
        version.get("number")
        if isinstance(version, dict)
        else None
    )
    indices = stats.get("indices")
    store = indices.get("store") if isinstance(indices, dict) else None
    nodes = stats.get("nodes")
    fs = nodes.get("fs") if isinstance(nodes, dict) else None
    jvm = nodes.get("jvm") if isinstance(nodes, dict) else None

    return {
        "version": version_number,
        "cluster_status": health.get("status"),
        "number_of_nodes": health.get("number_of_nodes"),
        "active_shards": health.get("active_shards"),
        "unassigned_shards": health.get("unassigned_shards"),
        "indices_store_bytes": (
            store.get("size_in_bytes")
            if isinstance(store, dict)
            else None
        ),
        "filesystem": {
            "total_bytes": (
                fs.get("total_in_bytes")
                if isinstance(fs, dict)
                else None
            ),
            "available_bytes": (
                fs.get("available_in_bytes")
                if isinstance(fs, dict)
                else None
            ),
        },
        "jvm": {
            "heap_used_bytes": (
                jvm.get("mem", {}).get("heap_used_in_bytes")
                if isinstance(jvm, dict)
                and isinstance(jvm.get("mem"), dict)
                else None
            ),
            "heap_max_bytes": (
                jvm.get("mem", {}).get("heap_max_in_bytes")
                if isinstance(jvm, dict)
                and isinstance(jvm.get("mem"), dict)
                else None
            ),
        },
    }


def _gate_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    encoder = bundle.get("encoder")
    host = bundle.get("host")
    elasticsearch = bundle.get("elasticsearch")
    reports = bundle.get("reports")

    queue = (
        encoder.get("queue")
        if isinstance(encoder, dict)
        and isinstance(encoder.get("queue"), dict)
        else {}
    )
    cache = (
        encoder.get("cache")
        if isinstance(encoder, dict)
        and isinstance(encoder.get("cache"), dict)
        else {}
    )
    swap = (
        host.get("swap_bytes")
        if isinstance(host, dict)
        and isinstance(host.get("swap_bytes"), dict)
        else {}
    )
    disk = (
        host.get("root_disk_bytes")
        if isinstance(host, dict)
        and isinstance(host.get("root_disk_bytes"), dict)
        else {}
    )

    baseline_report = (
        reports.get("baseline")
        if isinstance(reports, dict)
        and isinstance(reports.get("baseline"), dict)
        else {}
    )
    openvino_report = (
        reports.get("openvino")
        if isinstance(reports, dict)
        and isinstance(reports.get("openvino"), dict)
        else {}
    )
    rollback_report = (
        reports.get("rollback")
        if isinstance(reports, dict)
        and isinstance(reports.get("rollback"), dict)
        else {}
    )
    regression_report = (
        reports.get("regression")
        if isinstance(reports, dict)
        and isinstance(reports.get("regression"), dict)
        else {}
    )
    required_reports = {
        "baseline": bool(baseline_report.get("complete") is True),
        "openvino": bool(openvino_report.get("passed") is True),
        "ann": bool(isinstance(reports, dict) and reports.get("ann")),
        "load": bool(isinstance(reports, dict) and reports.get("load")),
        "rollback": bool(rollback_report.get("verified") is True),
        "regression": bool(regression_report.get("passed") is True),
    }
    optional_reports = {
        "int8": bool(isinstance(reports, dict) and reports.get("int8")),
    }
    return {
        "encoder_ready": bool(
            isinstance(encoder, dict)
            and encoder.get("status") == "ok"
        ),
        "bounded_queue_visible": bool(
            isinstance(queue, dict)
            and isinstance(queue.get("max_queue_size"), int)
        ),
        "cache_metrics_visible": bool(
            isinstance(cache, dict)
            and "image" in cache
            and "text" in cache
        ),
        "elasticsearch_healthy": bool(
            isinstance(elasticsearch, dict)
            and elasticsearch.get("cluster_status") in {"green", "yellow"}
        ),
        "swap_used_bytes": swap.get("used"),
        "root_disk_free_bytes": disk.get("free"),
        "required_reports_present": required_reports,
        "optional_reports_present": optional_reports,
        "release_evidence_complete": (
            bool(
                isinstance(encoder, dict)
                and encoder.get("status") == "ok"
            )
            and all(required_reports.values())
            and bool(
                isinstance(elasticsearch, dict)
                and elasticsearch.get("cluster_status") in {"green", "yellow"}
            )
        ),
    }


def collect_release_evidence(
    *,
    encoder_ready_url: str,
    elasticsearch_url: str,
    encoder_pid: int | None = None,
    baseline_report: Path | None = None,
    openvino_report: Path | None = None,
    ann_report: Path | None = None,
    int8_report: Path | None = None,
    load_report: Path | None = None,
    rollback_report: Path | None = None,
    regression_report: Path | None = None,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    with httpx.Client(timeout=timeout_seconds) as client:
        encoder_response = client.get(encoder_ready_url)
        encoder_response.raise_for_status()
        encoder = encoder_response.json()
        if not isinstance(encoder, dict):
            raise RuntimeError("encoder ready response is malformed")
        elasticsearch = _elasticsearch_snapshot(client, elasticsearch_url)

    bundle: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "host": _host_snapshot(),
        "encoder": {
            **encoder,
            "rss_bytes": _process_rss_bytes(encoder_pid),
        },
        "elasticsearch": elasticsearch,
        "reports": {
            "baseline": _read_json(baseline_report),
            "openvino": _read_json(openvino_report),
            "ann": _read_json(ann_report),
            "int8": _read_json(int8_report),
            "load": _read_json(load_report),
            "rollback": _read_json(rollback_report),
            "regression": _read_json(regression_report),
        },
    }
    bundle["gates"] = _gate_summary(bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect read-only CPU Visual Search release evidence. "
            "This command performs no rollout, restart, index mutation, or alias switch."
        )
    )
    parser.add_argument(
        "--encoder-ready-url",
        default="http://127.0.0.1:8091/ready",
    )
    parser.add_argument(
        "--elasticsearch-url",
        default="http://127.0.0.1:9200",
    )
    parser.add_argument("--encoder-pid", type=int)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--openvino-report", type=Path)
    parser.add_argument("--ann-report", type=Path)
    parser.add_argument("--int8-report", type=Path)
    parser.add_argument("--load-report", type=Path)
    parser.add_argument("--rollback-report", type=Path)
    parser.add_argument("--regression-report", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    bundle = collect_release_evidence(
        encoder_ready_url=args.encoder_ready_url,
        elasticsearch_url=args.elasticsearch_url,
        encoder_pid=args.encoder_pid,
        baseline_report=args.baseline_report,
        openvino_report=args.openvino_report,
        ann_report=args.ann_report,
        int8_report=args.int8_report,
        load_report=args.load_report,
        rollback_report=args.rollback_report,
        regression_report=args.regression_report,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
