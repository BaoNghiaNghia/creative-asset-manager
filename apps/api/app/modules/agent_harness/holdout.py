"""Phase 3C.1: prospective holdout design and objective QA evaluation kernel.

Pure, deterministic and offline: never reads production records, authorizes
callers, runs models, changes worker routing or promotes learning candidates.
Input aggregates are NOT trusted proof. A future experiment service must
pre-register the design, authenticate evidence, freeze arm assignment, verify
tenant/workflow lineage and independent blinded QA before using the result.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from app.modules.realistic_review_ugc.keyword_cohort_learning import COHORT_TERMS

_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$")
Arm = Literal["control", "challenger"]


class HoldoutValidationError(ValueError):
    """An invalid or unverifiable experiment contract."""


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise HoldoutValidationError(f"invalid {label}")
    return value


def assign_keyword_arm(
    *, tenant_id: str, cohort: str, study_version: str, keyword_id: str,
    holdout_percent: int = 20,
) -> Arm:
    """Deterministic keyword-level grouping, not independent per-output sampling.

    Historical Pick/Reject votes do NOT count as randomized holdout outcomes.
    This algorithm alone is not a pre-registered or tamper-resistant experiment.
    """
    _id(tenant_id, "tenant_id")
    if cohort not in COHORT_TERMS:
        raise HoldoutValidationError("invalid cohort")
    _id(study_version, "study_version")
    _id(keyword_id, "keyword_id")
    if type(holdout_percent) is not int or not 10 <= holdout_percent <= 40:
        raise HoldoutValidationError("invalid holdout percent")
    blob = json.dumps(
        ["cam-agent-holdout-v1", tenant_id, cohort, study_version, keyword_id],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    slot = int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") % 10_000
    return "challenger" if slot < holdout_percent * 100 else "control"


@dataclass(frozen=True)
class QualityArm:
    """Trusted metrics must originate from the independent QA telemetry store."""

    reviewed: int
    accepted: int
    independent_keyword_count: int
    critical_defects: int
    cost_per_accepted_usd: float

    def __post_init__(self) -> None:
        nums = (self.reviewed, self.accepted, self.independent_keyword_count, self.critical_defects)
        if any(type(item) is not int or item < 0 for item in nums):
            raise HoldoutValidationError("invalid QA counts")
        if self.accepted > self.reviewed or self.critical_defects > self.reviewed:
            raise HoldoutValidationError("inconsistent QA counts")
        if self.independent_keyword_count > self.reviewed:
            raise HoldoutValidationError("inconsistent keyword groups")
        if (type(self.cost_per_accepted_usd) not in (int, float)
                or not math.isfinite(self.cost_per_accepted_usd)
                or self.cost_per_accepted_usd < 0):
            raise HoldoutValidationError("invalid cost")


@dataclass(frozen=True)
class StudyVerification:
    """Provenance attestations, only meaningful after server-side validation."""

    preregistered_before_generation: bool
    assignments_frozen: bool
    source_lineage_verified: bool
    keyword_groups_disjoint: bool
    evaluation_artifacts_disjoint: bool
    blinded_qa_independent: bool
    security_passed: bool
    regression_free: bool

    def __post_init__(self) -> None:
        if any(type(getattr(self, field)) is not bool for field in (
            "preregistered_before_generation", "assignments_frozen",
            "source_lineage_verified", "keyword_groups_disjoint",
            "evaluation_artifacts_disjoint", "blinded_qa_independent",
            "security_passed", "regression_free",
        )):
            raise HoldoutValidationError("invalid attestation")


@dataclass(frozen=True)
class QualityAssessment:
    decision: Literal["blocked", "needs_more_samples", "no_verified_gain", "manual_review"]
    blockers: tuple[str, ...]
    baseline_acceptance: float
    challenger_acceptance: float
    conservative_gain: float
    promotion_allowed: bool = False


def _wilson_bounds(successes: int, trials: int) -> tuple[float, float]:
    """95% Wilson interval; zero trials are never accepted for evaluation."""
    if trials <= 0:
        return (0.0, 1.0)
    z = 1.959963984540054
    p = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    half = z * math.sqrt((p * (1.0 - p) + z2 / (4 * trials)) / trials) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def assess_independent_holdout(
    *, baseline: QualityArm, challenger: QualityArm,
    verification: StudyVerification,
    min_reviewed_per_arm: int = 40,
    min_keywords_per_arm: int = 10,
    min_conservative_gain: float = 0.02,
    max_cost_multiplier: float = 1.10,
) -> QualityAssessment:
    """Assess only independently verified, pre-registered challenger outcomes.

    Conservative gain = lower 95% Wilson bound (challenger) minus upper 95%
    Wilson bound (baseline). Positive gains only flag human review; never
    auto-enable learning or override production Creative Brief.
    """
    if (type(min_reviewed_per_arm) is not int or min_reviewed_per_arm < 30
            or type(min_keywords_per_arm) is not int or min_keywords_per_arm < 8):
        raise HoldoutValidationError("unsafe minimum sample threshold")
    if (type(min_conservative_gain) not in (int, float)
            or not math.isfinite(min_conservative_gain)
            or not 0 <= min_conservative_gain <= 0.20):
        raise HoldoutValidationError("invalid minimum gain")
    if (type(max_cost_multiplier) not in (int, float)
            or not math.isfinite(max_cost_multiplier)
            or not 1 <= max_cost_multiplier <= 1.25):
        raise HoldoutValidationError("unsafe cost regression threshold")
    baseline_rate = baseline.accepted / baseline.reviewed if baseline.reviewed else 0.0
    challenger_rate = challenger.accepted / challenger.reviewed if challenger.reviewed else 0.0
    baseline_upper = _wilson_bounds(baseline.accepted, baseline.reviewed)[1]
    challenger_lower = _wilson_bounds(challenger.accepted, challenger.reviewed)[0]
    conservative_gain = challenger_lower - baseline_upper

    attestation_fields = (
        "preregistered_before_generation", "assignments_frozen",
        "source_lineage_verified", "keyword_groups_disjoint",
        "evaluation_artifacts_disjoint", "blinded_qa_independent",
        "security_passed", "regression_free",
    )
    blockers = tuple(
        field for field in attestation_fields if not getattr(verification, field)
    )
    if blockers:
        return QualityAssessment(
            "blocked", blockers, baseline_rate, challenger_rate, conservative_gain,
        )
    if (baseline.reviewed < min_reviewed_per_arm
            or challenger.reviewed < min_reviewed_per_arm
            or baseline.independent_keyword_count < min_keywords_per_arm
            or challenger.independent_keyword_count < min_keywords_per_arm):
        return QualityAssessment(
            "needs_more_samples", ("insufficient_independent_holdout",),
            baseline_rate, challenger_rate, conservative_gain,
        )
    if (challenger.critical_defects > baseline.critical_defects
            or challenger.critical_defects > 0):
        return QualityAssessment(
            "no_verified_gain", ("critical_qa_defect",),
            baseline_rate, challenger_rate, conservative_gain,
        )
    baseline_cost = baseline.cost_per_accepted_usd
    challenger_cost = challenger.cost_per_accepted_usd
    if baseline_cost <= 0 or challenger_cost <= 0:
        return QualityAssessment(
            "blocked", ("cost_telemetry_missing",),
            baseline_rate, challenger_rate, conservative_gain,
        )
    if challenger_cost > baseline_cost * max_cost_multiplier:
        return QualityAssessment(
            "no_verified_gain", ("cost_regression",),
            baseline_rate, challenger_rate, conservative_gain,
        )
    if conservative_gain <= min_conservative_gain:
        return QualityAssessment(
            "no_verified_gain", ("no_statistically_conservative_improvement",),
            baseline_rate, challenger_rate, conservative_gain,
        )
    return QualityAssessment(
        "manual_review", ("independent_owner_approval_required",),
        baseline_rate, challenger_rate, conservative_gain,
    )


def evaluation_readiness_from_audit(audit: dict) -> dict:
    """Read-only response: current candidates still lack prospective QA evidence."""
    safe_statuses = {
        "current_candidate", "stale_source", "unsupported_source",
        "unexpected_verification", "rejected", "promoted_requires_review", "not_checked",
    }
    items = []
    for row in audit["items"]:
        status = row["review_status"]
        if status not in safe_statuses:
            raise HoldoutValidationError("unknown source evidence status")
        blockers = []
        if status != "current_candidate":
            blockers.append("source_not_eligible_for_holdout")
        if row["sample_count"] < 10:
            blockers.append("insufficient_training_evidence")
        blockers.extend([
            "prospective_trial_not_registered",
            "blinded_independent_qa_not_available",
            "disjoint_holdout_not_verified",
            "outcome_improvement_not_verified",
            "owner_approval_not_granted",
        ])
        items.append({
            "event_key": row["event_key"],
            "agent_version": row["agent_version"],
            "rule_key": row["rule_key"],
            "source_current": bool(row["source_current"]),
            "candidate_status": status,
            "blockers": blockers,
            "trial_ready": False,
            "promotion_allowed": False,
        })
    return {
        "tenant_id": audit["tenant_id"],
        "cohort": audit["cohort"],
        "source_version": audit["source_version"],
        "evaluation_mode": "preflight_only",
        "result_count": audit["result_count"],
        "has_more": audit["has_more"],
        "candidate_count_needing_trial": sum(
            item["candidate_status"] == "current_candidate" for item in items
        ),
        "automatic_promotion_enabled": False,
        "agent_execution_enabled": False,
        "prospective_trial_registered": False,
        "independent_qa_verified": False,
        "items": items,
    }
