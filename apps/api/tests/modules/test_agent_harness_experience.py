"""Evidence-gated learning: negative and tenant isolation tests."""
from __future__ import annotations

import unittest

from app.modules.agent_harness.experience import (
    ExperienceCandidate, VerificationResult,
    evaluate_experience, select_experiences,
)


def candidate(**overrides: object) -> ExperienceCandidate:
    kwargs = dict(
        tenant_id="boostgame", agent_id="gatorhats.typography",
        agent_version="v1", scope_key="heritage-serif",
        rule_key="rule:serif-2-line", confidence=0.91, sample_count=16,
        event_ids=tuple(f"event-{i}" for i in range(16)),
        evidence_refs=("asset-sha256:verified",),
        verification_refs=("eval-run:holdout-123",),
    )
    kwargs.update(overrides)
    return ExperienceCandidate(**kwargs)


def proof(**overrides: object) -> VerificationResult:
    kwargs = dict(
        required_checks=3, passed_checks=3, security_passed=True,
        regression_free=True, outcome_improved=True, holdout_samples=15,
    )
    kwargs.update(overrides)
    return VerificationResult(**kwargs)


class TestExperiencePromotion(unittest.TestCase):
    def test_good_verified_evidence_promotes(self) -> None:
        decision = evaluate_experience(candidate(), proof())
        self.assertEqual(decision.status, "promoted")

    def test_self_report_without_evidence_never_promotes(self) -> None:
        decision = evaluate_experience(candidate(verification_refs=()), proof())
        self.assertEqual((decision.status, decision.reason), ("candidate", "evidence_incomplete"))

    def test_low_sample_or_duplicate_event_cannot_promote(self) -> None:
        for change in (
            {"sample_count": 1},
            {"event_ids": tuple("same" for _ in range(16))},
            {"confidence": 0.79},
        ):
            with self.subTest(change=change):
                self.assertEqual(evaluate_experience(candidate(**change), proof()).status, "candidate")
        self.assertEqual(evaluate_experience(candidate(), proof(holdout_samples=1)).status, "candidate")

    def test_security_regression_failed_verification_rejected(self) -> None:
        for outcome in (
            {"security_passed": False},
            {"regression_free": False},
            {"passed_checks": 2},
            {"required_checks": 0, "passed_checks": 0},
            {"outcome_improved": False},
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(evaluate_experience(candidate(), proof(**outcome)).status, "rejected")

    def test_tenant_agent_version_and_scope_isolation(self) -> None:
        decisions = [
            evaluate_experience(candidate(), proof()),
            evaluate_experience(candidate(tenant_id="other", rule_key="other"), proof()),
            evaluate_experience(candidate(agent_version="v2", rule_key="v2"), proof()),
            evaluate_experience(candidate(scope_key="other", rule_key="other-scope"), proof()),
        ]
        found = select_experiences(
            decisions, tenant_id="boostgame", agent_id="gatorhats.typography",
            agent_version="v1", scope_key="heritage-serif",
        )
        self.assertEqual([item.rule_key for item in found], ["rule:serif-2-line"])
        self.assertEqual(select_experiences(
            decisions, tenant_id="", agent_id="gatorhats.typography",
            agent_version="v1", scope_key="heritage-serif",
        ), [])


if __name__ == "__main__":
    unittest.main()
