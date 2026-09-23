from __future__ import annotations

from app.modules.visual_search.release_evidence import _gate_summary
from app.modules.visual_search.rollout_policy import (
    ROLLOUT_MODE_ENCODER_ONLY,
    ROLLOUT_MODE_FULL_MIGRATION,
    ROLLOUT_MODE_PROGRESSIVE_INDEXING,
)


def _bundle(*, include_all_reports: bool, rollout_mode: str = ROLLOUT_MODE_FULL_MIGRATION) -> dict:
    reports = {
        "baseline": {
            "complete": True,
            "host": {
                "root_disk_bytes": {
                    "free": 20 * 1024**3,
                }
            },
        },
        "openvino": {"passed": True},
        "ann": {"profiles": []},
        "load": {"request_latency_ms": {"p95": 100.0}},
        "rollback": {"verified": True},
        "regression": {"passed": True},
        "int8": None,
    }
    if not include_all_reports:
        reports["load"] = None
    return {
        "rollout_mode": rollout_mode,
        "encoder": {
            "status": "ok",
            "queue": {
                "max_queue_size": 8,
                "queue_wait_ms": {"p50": 1.0, "p95": 2.0, "max": 3.0},
            },
            "cache": {
                "image": {"entries": 2},
                "text": {"entries": 3},
            },
        },
        "host": {
            "swap_bytes": {"used": 0},
            "root_disk_bytes": {"free": 20_000_000_000},
        },
        "elasticsearch": {
            "cluster_status": "yellow",
        },
        "reports": reports,
    }


def test_full_migration_release_evidence_requires_ann_and_validated_reports() -> None:
    complete = _gate_summary(_bundle(include_all_reports=True))
    assert complete["rollout_mode"] == ROLLOUT_MODE_FULL_MIGRATION
    assert complete["release_evidence_complete"] is True
    assert complete["required_reports_present"] == {
        "baseline": True,
        "openvino": True,
        "load": True,
        "rollback": True,
        "regression": True,
        "ann": True,
    }

    missing_ann_bundle = _bundle(include_all_reports=True)
    missing_ann_bundle["reports"]["ann"] = None
    missing_ann = _gate_summary(missing_ann_bundle)
    assert missing_ann["release_evidence_complete"] is False
    assert missing_ann["required_reports_present"]["ann"] is False

    missing_load = _gate_summary(_bundle(include_all_reports=False))
    assert missing_load["release_evidence_complete"] is False
    assert missing_load["required_reports_present"]["load"] is False

    failed_baseline_bundle = _bundle(include_all_reports=True)
    failed_baseline_bundle["reports"]["baseline"]["complete"] = False
    failed_baseline = _gate_summary(failed_baseline_bundle)
    assert failed_baseline["release_evidence_complete"] is False
    assert failed_baseline["required_reports_present"]["baseline"] is False

    failed_openvino_bundle = _bundle(include_all_reports=True)
    failed_openvino_bundle["reports"]["openvino"]["passed"] = False
    failed_openvino = _gate_summary(failed_openvino_bundle)
    assert failed_openvino["release_evidence_complete"] is False
    assert failed_openvino["required_reports_present"]["openvino"] is False

    failed_rollback_bundle = _bundle(include_all_reports=True)
    failed_rollback_bundle["reports"]["rollback"]["verified"] = False
    failed_rollback = _gate_summary(failed_rollback_bundle)
    assert failed_rollback["release_evidence_complete"] is False
    assert failed_rollback["required_reports_present"]["rollback"] is False

    failed_regression_bundle = _bundle(include_all_reports=True)
    failed_regression_bundle["reports"]["regression"]["passed"] = False
    failed_regression = _gate_summary(failed_regression_bundle)
    assert failed_regression["release_evidence_complete"] is False
    assert failed_regression["required_reports_present"]["regression"] is False


def test_encoder_only_release_evidence_does_not_require_ann() -> None:
    bundle = _bundle(
        include_all_reports=True,
        rollout_mode=ROLLOUT_MODE_ENCODER_ONLY,
    )
    bundle["reports"]["ann"] = None

    summary = _gate_summary(bundle)

    assert summary["rollout_mode"] == ROLLOUT_MODE_ENCODER_ONLY
    assert summary["release_evidence_complete"] is True
    assert summary["required_reports_present"] == {
        "baseline": True,
        "openvino": True,
        "load": True,
        "rollback": True,
        "regression": True,
    }
    assert summary["optional_reports_present"]["ann"] is False

def test_progressive_indexing_requires_ann_but_not_historical_completion() -> None:
    bundle = _bundle(
        include_all_reports=True,
        rollout_mode=ROLLOUT_MODE_PROGRESSIVE_INDEXING,
    )

    summary = _gate_summary(bundle)

    assert summary["release_evidence_complete"] is True
    assert summary["required_reports_present"]["ann"] is True
    assert summary["historical_backfill"] == {
        "strategy": "continuous_best_effort",
        "completion_required_for_release": False,
    }
