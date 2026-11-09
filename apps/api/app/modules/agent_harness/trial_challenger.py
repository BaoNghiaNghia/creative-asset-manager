"""Phase 3C.3B2B: disabled-by-default, pre-registered Stage 1 challenger.

No client-supplied text, free-form LLM experience, prompt template, or
historical vote is inserted into the treatment. Only a small fixed allowlist
of human-feedback rule enums maps to reviewed static typography guidance.

This module selects a treatment *before* the Codex invocation. It does not
claim exposure; the worker must separately attest successful application.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.processing.handlers import ClaimedJob
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.keyword_cohort_learning import semantic_cohort
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)
from app.modules.realistic_review_ugc.stage1_design_learning import REASON_GUIDANCE

from .holdout import assign_keyword_arm
from .model import AgentExperienceModel, AgentHoldoutAssignmentModel, AgentHoldoutStudyModel
from .trial_exposure import control_capture_enabled
from .trial_registration import _agent_source_current, _digest_source

BASELINE_BRIEF_VERSION = "meaning-first-144-v3"
CHALLENGER_POLICY_VERSION = "trial-144-ch1"

_MODE_GUIDANCE = {
    "safe": (
        "Keep the one Typography-First design readable in two seconds. "
        "Prioritize a restrained premium editorial hierarchy and clear counters."
    ),
    "creative_twist": (
        "Give the four Creative Twist designs distinct structural changes "
        "in letter construction or word hierarchy, without adding mandatory icons."
    ),
    "explore": (
        "Make the four Explore designs distinct in typographic architecture "
        "and negative-space rhythm, not only palette or decoration."
    ),
}
_VARIANT_GUIDANCE = {
    "A": "Favor balanced two-line editorial typography and generous safe margins.",
    "B": "Explore a clean varsity contrast in relative letter scale and word rhythm.",
    "C": "Explore a legible script-with-serif interaction only where it helps meaning.",
    "D": "Explore custom but clearly readable letter proportions and signature lockup.",
}

# Freeze the *meaning* of rejection reasons in reviewed code, not arbitrary
# historical review strings or AI-written Experience evaluations.
_REJECTION_GUIDANCE = {
    key: REASON_GUIDANCE[key]
    for key in (
        "unreadable_text", "wrong_spelling", "poor_cap_fit", "generic_layout",
        "too_busy", "wrong_style", "duplicate", "poor_embroidery",
    )
}


class TrialChallengerConflict(ValueError):
    """Invalid reservation/worker proof; no attempt to silently use baseline."""


@dataclass(frozen=True)
class ChallengerPlan:
    tenant_id: str
    study_id: str
    keyword_id: str
    processing_job_id: str
    candidate_event_key: str
    candidate_source_digest: str
    rule_key: str
    keyword_sha256: str
    directive: str
    policy_version: str = CHALLENGER_POLICY_VERSION


def challenger_enabled(settings: object) -> bool:
    return bool(
        getattr(settings, "AGENT_HARNESS_TRIAL_CHALLENGER_ENABLED", False)
        and control_capture_enabled(settings)
        and not getattr(settings, "AI_EMERGENCY_STOP_ENABLED", False)
    )


def directive_for_rule(rule_key: str) -> str:
    """Return a versioned, entirely server-authored treatment directive."""
    if not isinstance(rule_key, str):
        raise TrialChallengerConflict("trial_policy_rule_unsupported")
    fields = rule_key.split(":")
    if len(fields) == 4 and fields[:2] == ["stage1", "pick"]:
        _root, _kind, mode, variant = fields
        if (mode in _MODE_GUIDANCE and variant in _VARIANT_GUIDANCE
                and not (mode == "safe" and variant != "A")):
            return (
                "CONTROLLED HOLDOUT CHALLENGER v1: Follow the existing nine-design "
                "1/4/4 schema and exact original quote. Treat these cross-keyword "
                "signals as an experimental hypothesis, not proven sales evidence. "
                + _MODE_GUIDANCE[mode] + " " + _VARIANT_GUIDANCE[variant]
            )
    if len(fields) == 3 and fields[:2] == ["stage1", "reject"]:
        reason = fields[2]
        if reason in _REJECTION_GUIDANCE:
            return (
                "CONTROLLED HOLDOUT CHALLENGER v1: Follow the existing nine-design "
                "1/4/4 schema and exact original quote. Do not treat negative "
                "feedback from other keywords as proof for this phrase. "
                + _REJECTION_GUIDANCE[reason]
            )
    raise TrialChallengerConflict("trial_policy_rule_unsupported")


def prepare_challenger_plan(
    session: Session, *, job: ClaimedJob, keyword_id: str,
    settings: object, brief_prompt_version: str,
) -> ChallengerPlan | None:
    """Fail closed before provider invocation for an opted-in challenger.

    Non-trial and control jobs continue existing baseline generation. A valid
    challenger must have a frozen prospective assignment, source digest and
    current candidate. An invalid claimed challenger never quietly falls back.
    """
    if not challenger_enabled(settings):
        return None
    if brief_prompt_version != BASELINE_BRIEF_VERSION:
        raise TrialChallengerConflict("trial_baseline_version_changed")
    manifest = job.payload.get("trial_shadow_admission")
    if manifest is None:
        return None
    if not isinstance(manifest, dict) or manifest.get("mode") != "shadow_only":
        raise TrialChallengerConflict("trial_shadow_manifest_invalid")
    if manifest.get("arm") == "control":
        return None
    if (
        manifest.get("arm") != "challenger"
        or any(manifest.get(field) is not False for field in (
            "treatment_applied", "exposure_verified", "independent_qa_verified",
        ))
    ):
        raise TrialChallengerConflict("trial_shadow_manifest_invalid")
    if (
        not job.tenant_id or not job.id or not keyword_id
        or not isinstance(job.lease_owner, str) or not job.lease_owner
        or len(job.lease_owner) > 96
        or job.job_type != "rrugc_creative_brief_generate"
        or job.entity_type != "rrugc_keyword_creative_brief"
        or job.payload.get("keyword_id") != keyword_id
        or job.payload.get("prompt_version") != BASELINE_BRIEF_VERSION
    ):
        raise TrialChallengerConflict("trial_worker_identity_invalid")

    task = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job.id,
        ProcessingJobModel.tenant_id == job.tenant_id,
        ProcessingJobModel.job_type == job.job_type,
        ProcessingJobModel.entity_type == job.entity_type,
        ProcessingJobModel.entity_id == job.entity_id,
    ).with_for_update())
    if (
        task is None or task.status != "processing"
        or task.claimed_by != job.lease_owner
        or task.cancellation_requested
        or task.payload_json != dict(job.payload)
    ):
        raise TrialChallengerConflict("trial_worker_lease_changed")
    study_id = manifest.get("study_id")
    if not isinstance(study_id, str) or not 4 <= len(study_id) <= 64:
        raise TrialChallengerConflict("trial_study_id_invalid")
    assignment = session.scalar(select(AgentHoldoutAssignmentModel).where(
        AgentHoldoutAssignmentModel.tenant_id == job.tenant_id,
        AgentHoldoutAssignmentModel.study_id == study_id,
        AgentHoldoutAssignmentModel.keyword_id == keyword_id,
    ).with_for_update())
    study = session.get(AgentHoldoutStudyModel, (job.tenant_id, study_id))
    if (
        assignment is None or assignment.status != "reserved"
        or assignment.arm != "challenger"
        or study is None or study.status != "registered"
        or study.trial_version != manifest.get("trial_version")
        or study.candidate_source_digest != manifest.get("candidate_source_digest")
        or assignment.arm != assign_keyword_arm(
            tenant_id=job.tenant_id, cohort=study.cohort,
            study_version=f"{study.trial_version}.{study.study_id}",
            keyword_id=keyword_id, holdout_percent=study.holdout_percent,
        )
    ):
        raise TrialChallengerConflict("trial_assignment_changed")
    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == job.tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ))
    brief = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.tenant_id == job.tenant_id,
        RrugcKeywordCreativeBriefModel.id == job.entity_id,
        RrugcKeywordCreativeBriefModel.keyword_id == keyword_id,
        RrugcKeywordCreativeBriefModel.processing_job_id == job.id,
    ))
    if (
        keyword is None or brief is None
        or brief.prompt_version != BASELINE_BRIEF_VERSION
        or brief.keyword_sha256 != hashlib.sha256(keyword.keyword.encode("utf-8")).hexdigest()
        or semantic_cohort(keyword.keyword) != study.cohort
    ):
        raise TrialChallengerConflict("trial_keyword_not_prospective")
    digest = hashlib.sha256(
        keyword.keyword_normalized.casefold().strip().encode("utf-8"),
    ).hexdigest() if keyword.keyword_normalized else ""
    if digest != assignment.keyword_sha256 or digest != manifest.get("keyword_sha256"):
        raise TrialChallengerConflict("trial_keyword_changed")

    candidate = session.get(AgentExperienceModel, (job.tenant_id, study.candidate_event_key))
    if (
        candidate is None or _digest_source(candidate) != study.candidate_source_digest
        or not _agent_source_current(
            session, tenant_id=job.tenant_id,
            cohort=study.cohort, candidate=candidate,
        )
    ):
        raise TrialChallengerConflict("trial_candidate_not_current")
    directive = directive_for_rule(candidate.rule_key)
    return ChallengerPlan(
        tenant_id=job.tenant_id, study_id=study_id, keyword_id=keyword_id,
        processing_job_id=job.id, candidate_event_key=candidate.event_key,
        candidate_source_digest=study.candidate_source_digest,
        rule_key=candidate.rule_key, keyword_sha256=digest,
        directive=directive,
    )
