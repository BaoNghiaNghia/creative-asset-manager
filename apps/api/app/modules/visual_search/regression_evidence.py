from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable, Sequence


@dataclass(frozen=True, slots=True)
class RegressionCheck:
    name: str
    targets: tuple[str, ...]
    pytest_args: tuple[str, ...] = ()


DEFAULT_CHECKS = (
    RegressionCheck(
        "visual_encoder",
        ("apps/visual_encoder/tests",),
    ),
    RegressionCheck(
        "visual_search",
        ("apps/api/tests/modules/visual_search",),
    ),
    RegressionCheck(
        "search_v3",
        ("apps/api/tests/modules/search",),
    ),
    RegressionCheck(
        "deployment",
        ("deploy/tests/test_vps_deployment.py",),
    ),
    RegressionCheck(
        "tenant_isolation",
        (
            "apps/api/tests/modules/visual_search/test_elasticsearch.py",
            "apps/api/tests/modules/visual_search/test_router.py",
        ),
        ("-k", "cross_tenant"),
    ),
    RegressionCheck(
        "queue_priority",
        ("apps/visual_encoder/tests/test_inference_queue.py",),
        (
            "-k",
            (
                "interactive_work_runs_before_queued_background_work or "
                "background_cannot_consume_interactive_reserved_slot"
            ),
        ),
    ),
    RegressionCheck(
        "startup_pinning",
        ("apps/visual_encoder/tests/test_siglip_runtime.py",),
        (
            "-k",
            (
                "rejects_any_non_pinned_snapshot_path or "
                "bootstrap_uses_runtime_factory"
            ),
        ),
    ),
    RegressionCheck(
        "alias_lifecycle",
        ("apps/api/tests/modules/visual_search/test_elasticsearch.py",),
        (
            "-k",
            (
                "switch_aliases or "
                "migrate_legacy_physical_to_alias or "
                "v1_and_siglip2_v2_use_distinct_index_namespaces"
            ),
        ),
    ),
)


def _summary_lines(value: str, *, limit: int = 8) -> list[str]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[-limit:]


def run_regression_checks(
    *,
    repo_root: Path,
    checks: Sequence[RegressionCheck] = DEFAULT_CHECKS,
    timeout_seconds: float = 300.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    root = repo_root.resolve()
    if not root.is_dir():
        raise RuntimeError("repository root is unavailable")

    rows: list[dict[str, object]] = []
    for check in checks:
        command = [
            sys.executable,
            "-m",
            "pytest",
            *check.targets,
            *check.pytest_args,
            "-q",
        ]
        started = perf_counter()
        try:
            result = runner(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code = int(result.returncode)
            stdout_summary = _summary_lines(result.stdout or "")
            stderr_summary = _summary_lines(result.stderr or "")
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            stdout_summary = _summary_lines(
                exc.stdout if isinstance(exc.stdout, str) else ""
            )
            stderr_summary = _summary_lines(
                exc.stderr if isinstance(exc.stderr, str) else ""
            )
            timed_out = True

        rows.append(
            {
                "name": check.name,
                "targets": list(check.targets),
                "pytest_args": list(check.pytest_args),
                "python": sys.executable,
                "exit_code": exit_code,
                "passed": exit_code == 0,
                "timed_out": timed_out,
                "duration_seconds": perf_counter() - started,
                "stdout_summary": stdout_summary,
                "stderr_summary": stderr_summary,
            }
        )

    return {
        "checks": rows,
        "passed": bool(rows) and all(bool(row["passed"]) for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded Visual Search CPU regression groups using the "
            "current Python environment and write a release-evidence JSON report."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_regression_checks(
        repo_root=args.repo_root,
        timeout_seconds=args.timeout_seconds,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
