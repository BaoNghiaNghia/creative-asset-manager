from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.modules.visual_search.rollout_policy import (
    ROLLOUT_MODE_ENCODER_ONLY,
    ROLLOUT_MODES,
    disk_rollout_policy,
    normalize_rollout_mode,
)


def _regression_check(
    regression: dict[str, Any],
    name: str,
) -> bool | None:
    checks = regression.get("checks")
    if not isinstance(checks, list):
        return None
    for row in checks:
        if (
            isinstance(row, dict)
            and row.get("name") == name
            and isinstance(row.get("passed"), bool)
        ):
            return bool(row["passed"])
    return None


def _gate(
    gate_id: str,
    status: str,
    reason: str,
    evidence: Any = None,
) -> dict[str, Any]:
    if status not in {"pass", "fail", "unknown", "not_applicable"}:
        raise ValueError("unsupported acceptance gate status")
    return {
        "id": gate_id,
        "status": status,
        "reason": reason,
        "evidence": evidence,
    }


def evaluate_acceptance_gates(
    release: dict[str, Any],
    *,
    rollout_mode: str | None = None,
) -> dict[str, Any]:
    bundle_mode = release.get("rollout_mode")
    if (
        rollout_mode is not None
        and isinstance(bundle_mode, str)
        and normalize_rollout_mode(rollout_mode)
        != normalize_rollout_mode(bundle_mode)
    ):
        raise ValueError(
            "rollout_mode does not match the release-evidence bundle"
        )
    mode = normalize_rollout_mode(
        rollout_mode if rollout_mode is not None else bundle_mode
    )
    disk_policy = disk_rollout_policy(mode)
    reports = release.get("reports")
    reports = reports if isinstance(reports, dict) else {}
    regression = reports.get("regression")
    regression = regression if isinstance(regression, dict) else {}
    rollback = reports.get("rollback")
    rollback = rollback if isinstance(rollback, dict) else {}
    ann = reports.get("ann")
    ann = ann if isinstance(ann, dict) else {}
    int8 = reports.get("int8")
    int8 = int8 if isinstance(int8, dict) else None
    load = reports.get("load")
    load = load if isinstance(load, dict) else {}
    baseline = reports.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}

    encoder = release.get("encoder")
    encoder = encoder if isinstance(encoder, dict) else {}
    queue = encoder.get("queue")
    queue = queue if isinstance(queue, dict) else {}
    host = release.get("host")
    host = host if isinstance(host, dict) else {}

    gates: list[dict[str, Any]] = []

    search_v3 = _regression_check(regression, "search_v3")
    gates.append(
        _gate(
            "search_v3_regression",
            "unknown" if search_v3 is None else ("pass" if search_v3 else "fail"),
            (
                "Search V3 regression evidence is missing."
                if search_v3 is None
                else "Search V3 regression group passed."
                if search_v3
                else "Search V3 regression group failed."
            ),
            search_v3,
        )
    )

    isolation = _regression_check(regression, "tenant_isolation")
    gates.append(
        _gate(
            "tenant_isolation",
            "unknown" if isolation is None else ("pass" if isolation else "fail"),
            (
                "Dedicated cross-tenant regression evidence is missing."
                if isolation is None
                else "Cross-tenant Visual Search regressions passed."
                if isolation
                else "Cross-tenant Visual Search regression failed."
            ),
            isolation,
        )
    )

    rollback_verified = rollback.get("verified")
    gates.append(
        _gate(
            "v1_rollback_available",
            (
                "pass"
                if rollback_verified is True
                else "fail"
                if rollback_verified is False
                else "unknown"
            ),
            (
                "SigLIP v1 model/index rollback proof is verified."
                if rollback_verified is True
                else "Rollback proof explicitly failed."
                if rollback_verified is False
                else "Rollback proof has not been collected."
            ),
            rollback_verified,
        )
    )

    deployment = _regression_check(regression, "deployment")
    bounded_queue = (
        isinstance(queue.get("max_queue_size"), int)
        and int(queue["max_queue_size"]) > 0
    )
    if deployment is False:
        bounded_status = "fail"
    elif deployment is True and bounded_queue:
        bounded_status = "pass"
    else:
        bounded_status = "unknown"
    gates.append(
        _gate(
            "bounded_encoder_capacity",
            bounded_status,
            (
                "Deployment contract passed and runtime exposes a bounded queue."
                if bounded_status == "pass"
                else "Deployment contract failed."
                if bounded_status == "fail"
                else "Need both deployment regression and runtime bounded-queue evidence."
            ),
            {
                "deployment_regression": deployment,
                "max_queue_size": queue.get("max_queue_size"),
            },
        )
    )

    saturation_fields = (
        "queue_full_total",
        "queue_timeout_total",
        "queue_wait_ms",
        "max_queue_size",
    )
    saturation_visible = all(field in queue for field in saturation_fields)
    encoder_tests = _regression_check(regression, "visual_encoder")
    if encoder_tests is False:
        saturation_status = "fail"
    elif encoder_tests is True and saturation_visible:
        saturation_status = "pass"
    else:
        saturation_status = "unknown"
    gates.append(
        _gate(
            "queue_saturation_bounded_observable",
            saturation_status,
            (
                "Queue saturation counters/wait metrics are visible and encoder regressions passed."
                if saturation_status == "pass"
                else "Encoder regression failed."
                if saturation_status == "fail"
                else "Runtime queue saturation evidence is incomplete."
            ),
            {
                "encoder_regression": encoder_tests,
                "visible_fields": [
                    field for field in saturation_fields if field in queue
                ],
            },
        )
    )

    pinning = _regression_check(regression, "startup_pinning")
    gates.append(
        _gate(
            "no_floating_model_download",
            "unknown" if pinning is None else ("pass" if pinning else "fail"),
            (
                "Pinned local-only startup regression passed."
                if pinning
                else "Pinned-startup regression failed."
                if pinning is False
                else "Pinned-startup regression evidence is missing."
            ),
            pinning,
        )
    )

    priority = _regression_check(regression, "queue_priority")
    gates.append(
        _gate(
            "interactive_outranks_backfill",
            "unknown" if priority is None else ("pass" if priority else "fail"),
            (
                "Interactive-priority and reserved-capacity regressions passed."
                if priority
                else "Interactive queue-priority regression failed."
                if priority is False
                else "Queue-priority regression evidence is missing."
            ),
            priority,
        )
    )

    profiles = ann.get("profiles")
    measured_relevance = bool(
        isinstance(profiles, list)
        and profiles
        and all(
            isinstance(row, dict)
            and isinstance(row.get("expected_recall"), dict)
            and isinstance(row.get("baseline_overlap"), dict)
            for row in profiles
        )
    )
    if mode == ROLLOUT_MODE_ENCODER_ONLY:
        gates.append(
            _gate(
                "knn_relevance_evidence",
                "not_applicable",
                (
                    "ANN relevance is not required for encoder_only because this "
                    "profile does not authorize backfill or alias activation."
                ),
                {"profile_count": 0},
            )
        )
    else:
        gates.append(
            _gate(
                "knn_relevance_evidence",
                "pass" if measured_relevance else "unknown",
                (
                    "ANN report includes measured expected recall and baseline overlap."
                    if measured_relevance
                    else "Representative ANN relevance evidence has not been attached."
                ),
                {
                    "profile_count": len(profiles)
                    if isinstance(profiles, list)
                    else 0
                },
            )
        )

    if int8 is None:
        gates.append(
            _gate(
                "int8_representative_sanity",
                "not_applicable",
                "INT8 is not promoted in the current release candidate.",
            )
        )
    else:
        case_count = int8.get("case_count")
        int8_passed = int8.get("passed")
        int8_ok = (
            isinstance(case_count, int)
            and case_count >= 50
            and int8_passed is True
        )
        gates.append(
            _gate(
                "int8_representative_sanity",
                "pass" if int8_ok else "fail",
                (
                    "INT8 sanity report passed with at least 50 representative cases."
                    if int8_ok
                    else "INT8 evidence does not meet the 50-case passing gate."
                ),
                {
                    "case_count": case_count,
                    "passed": int8_passed,
                },
            )
        )

    swap_pressure = load.get("swap_pressure")
    swap_pressure = (
        swap_pressure if isinstance(swap_pressure, dict) else {}
    )
    swap_review = swap_pressure.get("passed")
    gates.append(
        _gate(
            "ram_no_sustained_swap_pressure",
            (
                "pass"
                if swap_review is True
                else "fail"
                if swap_review is False
                else "unknown"
            ),
            (
                "Bounded load evidence stayed within the reviewed swap-growth threshold."
                if swap_review is True
                else "Bounded load evidence exceeded the reviewed swap-growth threshold."
                if swap_review is False
                else "Load evidence does not yet contain a reviewed swap-growth threshold/result."
            ),
            swap_pressure or None,
        )
    )

    disk = host.get("root_disk_bytes")
    disk = disk if isinstance(disk, dict) else {}
    disk_free = disk.get("free")
    baseline_host = baseline.get("host")
    baseline_host = baseline_host if isinstance(baseline_host, dict) else {}
    baseline_disk = baseline_host.get("root_disk_bytes")
    baseline_disk = baseline_disk if isinstance(baseline_disk, dict) else {}
    baseline_disk_free = baseline_disk.get("free")

    if mode == ROLLOUT_MODE_ENCODER_ONLY:
        if isinstance(baseline_disk_free, int) and isinstance(disk_free, int):
            disk_status = (
                "pass"
                if (
                    baseline_disk_free >= disk_policy.minimum_before_bytes
                    and disk_free >= disk_policy.minimum_after_bytes
                )
                else "fail"
            )
        else:
            disk_status = "unknown"
        disk_reason = (
            "Encoder-only disk headroom meets the 6 GiB pre-rollout and 4 GiB post-provision targets."
            if disk_status == "pass"
            else "Encoder-only disk headroom is below the 6 GiB pre-rollout or 4 GiB post-provision target."
            if disk_status == "fail"
            else "Encoder-only disk evidence needs both baseline and current free-space measurements."
        )
    else:
        if isinstance(disk_free, int):
            disk_status = (
                "pass"
                if disk_free >= disk_policy.minimum_after_bytes
                else "fail"
            )
        else:
            disk_status = "unknown"
        disk_reason = (
            "Root disk meets the 12 GiB full-migration headroom target."
            if disk_status == "pass"
            else "Root disk is below the 12 GiB full-migration headroom target."
            if disk_status == "fail"
            else "Root disk free-space evidence is missing."
        )

    gates.append(
        _gate(
            "disk_migration_headroom",
            disk_status,
            disk_reason,
            {
                "rollout_mode": mode,
                "baseline_free_bytes": baseline_disk_free,
                "current_free_bytes": disk_free,
                "minimum_before_bytes": disk_policy.minimum_before_bytes,
                "minimum_after_bytes": disk_policy.minimum_after_bytes,
                "permits_full_migration": disk_policy.permits_full_migration,
            },
        )
    )

    alias_lifecycle = _regression_check(regression, "alias_lifecycle")
    if mode == ROLLOUT_MODE_ENCODER_ONLY:
        gates.append(
            _gate(
                "elasticsearch_alias_rollback",
                "not_applicable",
                (
                    "Elasticsearch alias activation is not authorized by "
                    "encoder_only; the previous v1 rollback target is still "
                    "required separately."
                ),
                {
                    "alias_lifecycle_regression": alias_lifecycle,
                    "rollback_verified": rollback_verified,
                },
            )
        )
    else:
        if rollback_verified is False or alias_lifecycle is False:
            alias_status = "fail"
        elif rollback_verified is True and alias_lifecycle is True:
            alias_status = "pass"
        else:
            alias_status = "unknown"
        gates.append(
            _gate(
                "elasticsearch_alias_rollback",
                alias_status,
                (
                    "Alias lifecycle regression passed and the previous v1 rollback target is verified."
                    if alias_status == "pass"
                    else "Alias rollback/lifecycle evidence failed."
                    if alias_status == "fail"
                    else "Need both alias-lifecycle regression and verified rollback target."
                ),
                {
                    "alias_lifecycle_regression": alias_lifecycle,
                    "rollback_verified": rollback_verified,
                },
            )
        )

    counts = {
        status: sum(1 for gate in gates if gate["status"] == status)
        for status in ("pass", "fail", "unknown", "not_applicable")
    }
    release_gate_summary = release.get("gates")
    release_gate_summary = (
        release_gate_summary
        if isinstance(release_gate_summary, dict)
        else {}
    )
    prerequisites = {
        "baseline_complete": baseline.get("complete") is True,
        "release_evidence_complete": (
            release_gate_summary.get("release_evidence_complete") is True
        ),
    }
    prerequisites_complete = all(prerequisites.values())
    complete = (
        prerequisites_complete
        and counts["fail"] == 0
        and counts["unknown"] == 0
    )
    if complete:
        decision = (
            "eligible_for_encoder_only_rollout_review"
            if mode == ROLLOUT_MODE_ENCODER_ONLY
            else "eligible_for_release_review"
        )
    else:
        decision = "blocked_pending_evidence_or_failure"
    return {
        "rollout_mode": mode,
        "permits_full_migration": disk_policy.permits_full_migration,
        "gates": gates,
        "counts": counts,
        "prerequisites": prerequisites,
        "prerequisites_complete": prerequisites_complete,
        "acceptance_complete": complete,
        "decision": decision,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the documented Visual Search CPU acceptance gates from "
            "an immutable release-evidence JSON bundle."
        )
    )
    parser.add_argument("--release-evidence", type=Path, required=True)
    parser.add_argument(
        "--rollout-mode",
        choices=ROLLOUT_MODES,
        help=(
            "Optional explicit mode. When the release bundle already declares "
            "a mode, this value must match it."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        release = json.loads(
            args.release_evidence.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("release evidence is unavailable or invalid") from exc
    if not isinstance(release, dict):
        raise RuntimeError("release evidence must contain a JSON object")

    result = evaluate_acceptance_gates(
        release,
        rollout_mode=args.rollout_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0 if result["acceptance_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
