from __future__ import annotations

import pytest

from app.modules.visual_search.acceptance_gates import (
    evaluate_acceptance_gates,
)
from app.modules.visual_search.rollout_policy import (
    ROLLOUT_MODE_ENCODER_ONLY,
    ROLLOUT_MODE_FULL_MIGRATION,
    ROLLOUT_MODE_PROGRESSIVE_INDEXING,
)


def _release_bundle(
    *,
    rollout_mode: str = ROLLOUT_MODE_FULL_MIGRATION,
    baseline_free_gib: float = 20,
    current_free_gib: float = 20,
) -> dict:
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
        "rollout_mode": rollout_mode,
        "host": {
            "root_disk_bytes": {
                "free": int(current_free_gib * 1024**3),
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
            "rollout_mode": rollout_mode,
            "release_evidence_complete": True,
        },
        "reports": {
            "baseline": {
                "complete": True,
                "host": {
                    "root_disk_bytes": {
                        "free": int(baseline_free_gib * 1024**3),
                    }
                },
            },
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


def test_full_migration_acceptance_requires_all_evidence() -> None:
    result = evaluate_acceptance_gates(_release_bundle())

    assert result["rollout_mode"] == ROLLOUT_MODE_FULL_MIGRATION
    assert result["permits_full_migration"] is True
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


def test_full_migration_still_fails_at_8_3_gib() -> None:
    release = _release_bundle(
        baseline_free_gib=8.3,
        current_free_gib=8.3,
    )

    result = evaluate_acceptance_gates(release)
    disk = {row["id"]: row for row in result["gates"]}[
        "disk_migration_headroom"
    ]

    assert result["acceptance_complete"] is False
    assert disk["status"] == "fail"
    assert disk["evidence"]["minimum_after_bytes"] == 12 * 1024**3
    assert disk["evidence"]["permits_full_migration"] is True


def test_encoder_only_accepts_low_disk_profile_without_ann_or_alias_activation() -> None:
    release = _release_bundle(
        rollout_mode=ROLLOUT_MODE_ENCODER_ONLY,
        baseline_free_gib=8.3,
        current_free_gib=5.0,
    )
    release["reports"]["ann"] = None

    result = evaluate_acceptance_gates(release)
    by_id = {row["id"]: row for row in result["gates"]}

    assert result["rollout_mode"] == ROLLOUT_MODE_ENCODER_ONLY
    assert result["permits_full_migration"] is False
    assert result["acceptance_complete"] is True
    assert result["decision"] == "eligible_for_encoder_only_rollout_review"
    assert result["counts"] == {
        "pass": 9,
        "fail": 0,
        "unknown": 0,
        "not_applicable": 3,
    }
    assert by_id["disk_migration_headroom"]["status"] == "pass"
    assert by_id["disk_migration_headroom"]["evidence"] == {
        "rollout_mode": ROLLOUT_MODE_ENCODER_ONLY,
        "baseline_free_bytes": int(8.3 * 1024**3),
        "current_free_bytes": 5 * 1024**3,
        "minimum_before_bytes": 6 * 1024**3,
        "minimum_after_bytes": 4 * 1024**3,
        "permits_full_migration": False,
    }
    assert by_id["knn_relevance_evidence"]["status"] == "not_applicable"
    assert by_id["elasticsearch_alias_rollback"]["status"] == "not_applicable"


@pytest.mark.parametrize(
    ("baseline_free_gib", "current_free_gib"),
    [
        (5.9, 5.0),
        (8.3, 3.9),
    ],
)
def test_encoder_only_fails_closed_below_either_disk_threshold(
    baseline_free_gib: float,
    current_free_gib: float,
) -> None:
    release = _release_bundle(
        rollout_mode=ROLLOUT_MODE_ENCODER_ONLY,
        baseline_free_gib=baseline_free_gib,
        current_free_gib=current_free_gib,
    )
    release["reports"]["ann"] = None

    result = evaluate_acceptance_gates(release)
    disk = {row["id"]: row for row in result["gates"]}[
        "disk_migration_headroom"
    ]

    assert result["acceptance_complete"] is False
    assert disk["status"] == "fail"


def test_acceptance_gates_fail_closed_for_unknown_swap() -> None:
    release = _release_bundle()
    release["reports"]["load"]["swap_pressure"]["passed"] = None

    result = evaluate_acceptance_gates(release)
    by_id = {row["id"]: row for row in result["gates"]}

    assert result["acceptance_complete"] is False
    assert result["decision"] == "blocked_pending_evidence_or_failure"
    assert by_id["ram_no_sustained_swap_pressure"]["status"] == "unknown"


def test_acceptance_rejects_rollout_mode_mismatch() -> None:
    release = _release_bundle(rollout_mode=ROLLOUT_MODE_ENCODER_ONLY)

    with pytest.raises(ValueError, match="does not match"):
        evaluate_acceptance_gates(
            release,
            rollout_mode=ROLLOUT_MODE_FULL_MIGRATION,
        )


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

def test_progressive_indexing_does_not_require_complete_historical_backfill() -> None:
    release = _release_bundle(
        rollout_mode=ROLLOUT_MODE_PROGRESSIVE_INDEXING,
        baseline_free_gib=8.3,
        current_free_gib=6.5,
    )

    result = evaluate_acceptance_gates(release)
    by_id = {row["id"]: row for row in result["gates"]}

    assert result["rollout_mode"] == ROLLOUT_MODE_PROGRESSIVE_INDEXING
    assert result["permits_full_migration"] is False
    assert result["historical_backfill_completion_required"] is False
    assert result["acceptance_complete"] is True
    assert result["decision"] == "eligible_for_progressive_indexing_review"
    assert by_id["knn_relevance_evidence"]["status"] == "pass"
    assert by_id["elasticsearch_alias_rollback"]["status"] == "pass"
    assert by_id["disk_migration_headroom"]["evidence"]["minimum_before_bytes"] == 8 * 1024**3
    assert by_id["disk_migration_headroom"]["evidence"]["minimum_after_bytes"] == 6 * 1024**3
