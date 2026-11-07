"""CAM experience promotion gate - pure policy, no database or job side effects."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class ExperienceCandidate:
    tenant_id: str
    agent_id: str
    agent_version: str
    scope_key: str
    rule_key: str
    confidence: float
    sample_count: int
    event_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    verification_refs: tuple[str, ...]


@dataclass(frozen=True)
class VerificationResult:
    required_checks: int
    passed_checks: int
    security_passed: bool
    regression_free: bool
    outcome_improved: bool
    holdout_samples: int


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    reason: str
    candidate: ExperienceCandidate


def evaluate_experience(
    candidate: ExperienceCandidate, verification: VerificationResult,
    *, minimum_samples: int = 10,
) -> PromotionDecision:
    """Fail closed unless independently verified, sufficient evidence exists.

    Persistence must independently verify tenant ownership, event authenticity,
    source/output lineage and evaluation holdout separation.
    """
    if not all((candidate.tenant_id, candidate.agent_id, candidate.agent_version,
                candidate.scope_key, candidate.rule_key)):
        return PromotionDecision("rejected", "invalid_scope", candidate)
    if (type(candidate.confidence) not in (int, float)
            or not isfinite(candidate.confidence)
            or not 0 <= candidate.confidence <= 1):
        return PromotionDecision("rejected", "invalid_confidence", candidate)
    if (not verification.security_passed or not verification.regression_free
            or verification.required_checks < 1
            or verification.passed_checks != verification.required_checks):
        return PromotionDecision("rejected", "verification_failed", candidate)
    if not verification.outcome_improved:
        return PromotionDecision("rejected", "no_verified_improvement", candidate)
    if not candidate.evidence_refs or not candidate.verification_refs:
        return PromotionDecision("candidate", "evidence_incomplete", candidate)
    threshold = max(10, minimum_samples) if type(minimum_samples) is int else 10
    if (type(candidate.sample_count) is not int
            or not isinstance(candidate.event_ids, tuple)
            or any(not isinstance(item, str) or not item.strip() for item in candidate.event_ids)
            or candidate.sample_count < threshold
            or len(set(candidate.event_ids)) < threshold
            or verification.holdout_samples < threshold):
        return PromotionDecision("candidate", "insufficient_samples", candidate)
    if candidate.confidence < 0.8:
        return PromotionDecision("candidate", "low_confidence", candidate)
    return PromotionDecision("promoted", "verified_experience", candidate)


def select_experiences(
    decisions: list[PromotionDecision], *, tenant_id: str,
    agent_id: str, agent_version: str, scope_key: str, limit: int = 8,
) -> list[ExperienceCandidate]:
    """Only exact-scope, verified rules; never broaden tenant or version scope."""
    if not all((tenant_id, agent_id, agent_version, scope_key)):
        return []
    matches = [
        decision.candidate for decision in decisions
        if decision.status == "promoted"
        and decision.candidate.tenant_id == tenant_id
        and decision.candidate.agent_id == agent_id
        and decision.candidate.agent_version == agent_version
        and decision.candidate.scope_key == scope_key
    ]
    matches.sort(key=lambda item: (-item.confidence, item.rule_key))
    return matches[:max(0, min(limit, 32))]
