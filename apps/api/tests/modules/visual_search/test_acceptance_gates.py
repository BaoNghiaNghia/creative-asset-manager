from __future__ import annotations

from app.modules.visual_search.acceptance_gates import (
    evaluate_acceptance_gates,
)


def _release_bundle() -> dict:
    checks = [
        {"name": "visual_encoder", "passed": True},
        {"name": "search_v3", "passed": True},
        {"name": "deployment", "passed": True},
        {"name": "tenant_isolation", "passed": True},
        {"name": "queue_priority", "passed": True},
        {"name": "startup_pinning", "passed": True},
        {"name": "alias_lifecycle", "passed": True},
    ]
    return {
        "host": {
            "root_disk_bytes": {
                "free": 20 * 1024**3,
            }
        },
        "encoder": {
            "queue": {
                "max_queue_size": 8,
                "queue_full_total": 0,
                "queue_timeout_total": 0,
                "queue_wait_ms": {
                    "sample_count": 20,
                    "p50": 1.0,
                    "p95": 2.0,
                    "max": 3.0,
                },
            }
        },
        "gates": {
            "release_evidence_complete": True,
        },
        "reports": {
            "baseline": {"complete": True},
            "regression": {
                "passed": True,
                "checks": checks,
            },
            "rollback": {"verified": True},
            "ann": {
                "profiles": [
                    {
                        "name": "A",
                        "expected_recall": {"mean": 1.0, "min": 1.0},
                        "baseline_overlap": {"mean": 1.0, "min": 1.0},
                    }
                ]
            },
            "load": {
                "swap_pressure": {
                    "growth_bytes": 0,
                    "max_allowed_growth_bytes": 0,
                    "passed": True,
                }
            },
            "int8": None,
        },
    }


def test_acceptance_gates_complete_only_with_all_required_evidence() -> None:
    result = evaluate_acceptance_gates(_release_bundle())

    assert result["prerequisites_complete"] is True
    assert result["prerequisites"] == {
        "baseline_complete": True,
        "release_evidence_complete": True,
    }
    assert result["acceptance_complete"] is True
    assert result["decision"] == "eligible_for_release_review"
    assert result["counts"] == {
        "pass": 11,
        "fail": 0,
        "unknown": 0,
        "not_applicable": 1,
    }


def test_acceptance_gates_fail_closed_for_disk_and_unknown_swap() -> None:
    release = _release_bundle()
    release["host"]["root_disk_bytes"]["free"] = 8 * 1024**3
    release["reports"]["load"]["swap_pressure"]["passed"] = None

    result = evaluate_acceptance_gates(release)
    by_id = {row["id"]: row for row in result["gates"]}

    assert result["acceptance_complete"] is False
    assert result["decision"] == "blocked_pending_evidence_or_failure"
    assert by_id["disk_migration_headroom"]["status"] == "fail"
    assert by_id["ram_no_sustained_swap_pressure"]["status"] == "unknown"


def test_int8_gate_requires_fifty_passing_cases_when_candidate_is_present() -> None:
    release = _release_bundle()
    release["reports"]["int8"] = {
        "case_count": 49,
        "passed": True,
    }

    result = evaluate_acceptance_gates(release)
    by_id = {row["id"]: row for row in result["gates"]}

    assert by_id["int8_representative_sanity"]["status"] == "fail"
    assert result["acceptance_complete"] is False
