"""Phase 3C.1 no-side-effect holdout contract and readiness API regression."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.agent_harness.model import AgentDefinitionModel
from app.modules.agent_harness.holdout import (
    HoldoutValidationError, QualityArm, StudyVerification,
    assess_independent_holdout, assign_keyword_arm, evaluation_readiness_from_audit,
)
from tests.modules.test_agent_harness_evidence_audit import (
    scenario, seed, register_current,
)


URL = "/api/v1/admin/agent-harness/boostgame/feedback/stage1/evaluation-readiness?cohort=music"


def arm(*, reviewed=80, accepted=48, independent_keyword_count=18,
        critical_defects=0, cost_per_accepted_usd=1.0):
    return QualityArm(
        reviewed=reviewed, accepted=accepted,
        independent_keyword_count=independent_keyword_count,
        critical_defects=critical_defects, cost_per_accepted_usd=cost_per_accepted_usd,
    )


def valid_proof():
    return StudyVerification(
        preregistered_before_generation=True, assignments_frozen=True,
        source_lineage_verified=True, keyword_groups_disjoint=True,
        evaluation_artifacts_disjoint=True, blinded_qa_independent=True,
        security_passed=True, regression_free=True,
    )


def test_keyword_level_assignment_stable_and_scoped():
    keyword = "9371d1e0-744d-43aa-a249-f0c9202e3e5d"
    params = dict(tenant_id="boostgame", cohort="music", study_version="v1", keyword_id=keyword)
    assert assign_keyword_arm(**params) == assign_keyword_arm(**params)
    baseline = [
        assign_keyword_arm(tenant_id="boostgame", cohort="music",
                           study_version="v1", keyword_id=f"keyword-{n}")
        for n in range(1000)
    ]
    holdouts = baseline.count("challenger")
    assert 140 <= holdouts <= 260
    switched = [
        assign_keyword_arm(tenant_id="other", cohort="music",
                           study_version="v1", keyword_id=f"keyword-{n}")
        for n in range(1000)
    ]
    assert baseline != switched
    assert all(item in ("control", "challenger") for item in baseline)


@pytest.mark.parametrize("updates", [
    {"tenant_id": ""}, {"tenant_id": "../bad"}, {"cohort": "unknown"},
    {"study_version": "bad version"}, {"keyword_id": "unsafe/keyword"},
    {"holdout_percent": 0}, {"holdout_percent": 100},
    {"holdout_percent": True}, {"holdout_percent": 0.2},
])
def test_assignment_invalid_input_fails_closed(updates):
    args = dict(tenant_id="boostgame", cohort="music", study_version="v1",
                keyword_id="kw-123", holdout_percent=20)
    args.update(updates)
    with pytest.raises(HoldoutValidationError):
        assign_keyword_arm(**args)


def test_independent_well_supported_gain_requires_manual_review():
    result = assess_independent_holdout(
        baseline=arm(), challenger=arm(accepted=77, cost_per_accepted_usd=1.03),
        verification=valid_proof(),
    )
    assert result.decision == "manual_review"
    assert result.conservative_gain > .02
    assert not result.promotion_allowed
    assert result.blockers == ("independent_owner_approval_required",)


@pytest.mark.parametrize("field", [
    "preregistered_before_generation", "assignments_frozen", "source_lineage_verified",
    "keyword_groups_disjoint", "evaluation_artifacts_disjoint",
    "blinded_qa_independent", "security_passed", "regression_free",
])
def test_unverified_attestation_fails_closed(field):
    proof = replace(valid_proof(), **{field: False})
    result = assess_independent_holdout(
        baseline=arm(), challenger=arm(accepted=77),
        verification=proof,
    )
    assert result.decision == "blocked"
    assert field in result.blockers
    assert not result.promotion_allowed


@pytest.mark.parametrize("challenger,decision,blocker", [
    (arm(reviewed=20, accepted=18, independent_keyword_count=9), "needs_more_samples", "insufficient_independent_holdout"),
    (arm(reviewed=80, accepted=52), "no_verified_gain", "no_statistically_conservative_improvement"),
    (arm(reviewed=80, accepted=77, critical_defects=1), "no_verified_gain", "critical_qa_defect"),
    (arm(reviewed=80, accepted=77, cost_per_accepted_usd=1.5), "no_verified_gain", "cost_regression"),
    (arm(reviewed=80, accepted=77, cost_per_accepted_usd=0), "blocked", "cost_telemetry_missing"),
])
def test_insufficient_regressing_or_untrusted_metrics_block(challenger, decision, blocker):
    result = assess_independent_holdout(
        baseline=arm(), challenger=challenger, verification=valid_proof(),
    )
    assert result.decision == decision
    assert blocker in result.blockers
    assert not result.promotion_allowed


@pytest.mark.parametrize("kwargs", [
    {"reviewed": -1}, {"reviewed": True}, {"accepted": -1},
    {"accepted": 90}, {"critical_defects": 81},
    {"independent_keyword_count": 81}, {"cost_per_accepted_usd": -1},
    {"cost_per_accepted_usd": float("inf")},
    {"cost_per_accepted_usd": float("nan")},
])
def test_invalid_quality_metrics_rejected(kwargs):
    with pytest.raises(HoldoutValidationError):
        arm(**kwargs)


def test_invalid_attestation_type_rejected():
    with pytest.raises(HoldoutValidationError):
        replace(valid_proof(), security_passed="yes")


@pytest.mark.parametrize("params", [
    {"min_reviewed_per_arm": 1}, {"min_keywords_per_arm": 1},
    {"min_conservative_gain": -1}, {"min_conservative_gain": float("nan")},
    {"max_cost_multiplier": 1.6}, {"max_cost_multiplier": True},
])
def test_unsafe_thresholds_rejected(params):
    with pytest.raises(HoldoutValidationError):
        assess_independent_holdout(
            baseline=arm(), challenger=arm(accepted=77),
            verification=valid_proof(), **params,
        )


def test_preflight_reports_no_prospective_trial_and_does_not_promote(scenario):
    client, db, principal, flags = scenario
    seed(db)
    register_current(db)
    assert client.get(URL).status_code == 404
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluation_mode"] == "preflight_only"
    assert body["candidate_count_needing_trial"] == 1
    assert body["result_count"] == 1
    assert body["automatic_promotion_enabled"] is False
    assert body["agent_execution_enabled"] is False
    assert body["prospective_trial_registered"] is False
    assert body["independent_qa_verified"] is False
    row = body["items"][0]
    assert row["candidate_status"] == "current_candidate"
    assert "prospective_trial_not_registered" in row["blockers"]
    assert "blinded_independent_qa_not_available" in row["blockers"]
    assert "owner_approval_not_granted" in row["blockers"]
    assert row["promotion_allowed"] is False
    assert "music guitar" not in response.text
    assert "stage1-output:" not in response.text
    assert "sha256:" not in response.text
    assert db.get(AgentDefinitionModel, ("boostgame", "gatorhats.feedback-intelligence", "v1")).enabled is False


def test_readiness_honors_both_permissions_tenant_and_input(scenario):
    client, db, principal, flags = scenario
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    assert client.get(URL).status_code == 200
    principal.effective_permissions = frozenset({"ai_operations.read"})
    assert client.get(URL).status_code == 403
    principal.effective_permissions = frozenset({"ai_provider.configure"})
    assert client.get(URL).status_code == 403
    principal.effective_permissions = frozenset({"ai_operations.read", "ai_provider.configure"})
    assert client.get(URL.replace("boostgame", "another")).status_code == 403
    assert client.get(URL.replace("cohort=music", "cohort=bad")).status_code == 422
    assert client.get(URL + "&limit=31").status_code == 422
    assert client.get(URL + "&limit=0").status_code == 422


def test_stale_source_remains_blocked(scenario):
    client, db, principal, flags = scenario
    outputs = seed(db)
    register_current(db)
    chosen = outputs[0]
    metadata = dict(chosen.font_info_json)
    review = dict(metadata["creative_feedback"])
    review["decision"] = "neutral"
    metadata["creative_feedback"] = review
    chosen.font_info_json = metadata
    db.commit()
    flags.AGENT_HARNESS_READ_API_ENABLED = True
    report = client.get(URL).json()
    assert report["items"][0]["candidate_status"] == "stale_source"
    assert "source_not_eligible_for_holdout" in report["items"][0]["blockers"]
    assert not report["items"][0]["promotion_allowed"]


def test_preflight_rejects_unknown_audit_status():
    data = {
        "tenant_id": "boostgame", "cohort": "music", "source_version": "v1",
        "result_count": 1, "has_more": False,
        "items": [{
            "review_status": "unknown", "sample_count": 12,
            "event_key": "hash", "agent_version": "v1",
            "rule_key": "rule", "source_current": True,
        }],
    }
    with pytest.raises(HoldoutValidationError):
        evaluation_readiness_from_audit(data)
