"""Phase 3C.3A: prospective, shadow-only admission at first Stage 1 queue.

This is NOT a trial exposure: neither control nor challenger receives a
different prompt. Actual exposure and independent QA must be recorded by a
separate treatment-aware worker before evaluating a candidate. No promotion.
All writes made by record_shadow_admission share the caller's transaction.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.keyword_cohort_learning import semantic_cohort
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordImageJobModel,
    RrugcKeywordVolumeModel,
)

from .holdout import assign_keyword_arm
from .model import (
    AgentExperienceModel,
    AgentHoldoutAssignmentModel,
    AgentHoldoutStudyModel,
)
from .trial_registration import _agent_source_current, _digest_source


class TrialAdmissionConflict(ValueError):
    """Fail closed without leaking trial source material or keyword text."""


@dataclass(frozen=True)
class ShadowAdmission:
    study_id: str
    keyword_id: str
    arm: str
    trial_version: str
    candidate_digest: str
    keyword_digest: str
    actor_id: str


def admission_enabled(settings: object) -> bool:
    """Separate opt-in; no change to old Stage 0/1 paths when disabled."""
    return bool(
        getattr(settings, "AGENT_HARNESS_READ_API_ENABLED", False)
        and getattr(settings, "AGENT_HARNESS_MANAGEMENT_API_ENABLED", False)
        and getattr(settings, "AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED", False)
        and getattr(settings, "AGENT_HARNESS_TRIAL_ADMISSION_ENABLED", False)
        and not getattr(settings, "AI_EMERGENCY_STOP_ENABLED", False)
    )


def prepare_shadow_admission(
    session: Session, *, tenant_id: str, keyword: RrugcKeywordVolumeModel,
    has_prior_brief: bool, settings: object, actor_id: str | None = None,
) -> ShadowAdmission | None:
    """Lock & revalidate a reservation before the very first Creative Brief.

    No outcome is observed. A pre-existing Brief or image job is forbidden for
    any prospective reservation, including version upgrades/regenerations.
    """
    if not admission_enabled(settings):
        return None
    assignment = session.scalar(
        select(AgentHoldoutAssignmentModel).where(
            AgentHoldoutAssignmentModel.tenant_id == tenant_id,
            AgentHoldoutAssignmentModel.keyword_id == keyword.id,
        ).with_for_update()
    )
    if assignment is None:
        return None
    if (has_prior_brief or assignment.status != "reserved"
            or not keyword.picked or keyword.tenant_id != tenant_id):
        raise TrialAdmissionConflict("trial_keyword_not_prospective")
    if session.scalar(
        select(RrugcKeywordImageJobModel.id).where(
            RrugcKeywordImageJobModel.tenant_id == tenant_id,
            RrugcKeywordImageJobModel.keyword_id == keyword.id,
        ).limit(1)
    ) is not None:
        raise TrialAdmissionConflict("trial_keyword_not_prospective")

    study = session.scalar(
        select(AgentHoldoutStudyModel).where(
            AgentHoldoutStudyModel.tenant_id == tenant_id,
            AgentHoldoutStudyModel.study_id == assignment.study_id,
        ).with_for_update()
    )
    if study is None or study.status != "registered":
        raise TrialAdmissionConflict("trial_study_not_registered")
    candidate = session.get(
        AgentExperienceModel, (tenant_id, study.candidate_event_key),
    )
    if (candidate is None
            or not _agent_source_current(
                session, tenant_id=tenant_id, cohort=study.cohort,
                candidate=candidate,
            )
            or study.candidate_source_digest != _digest_source(candidate)):
        raise TrialAdmissionConflict("trial_candidate_not_current")
    if semantic_cohort(keyword.keyword) != study.cohort:
        raise TrialAdmissionConflict("trial_keyword_cohort_changed")
    digest = hashlib.sha256(
        keyword.keyword_normalized.casefold().strip().encode("utf-8"),
    ).hexdigest() if keyword.keyword_normalized else ""
    arm = assign_keyword_arm(
        tenant_id=tenant_id, cohort=study.cohort,
        study_version=f"{study.trial_version}.{study.study_id}",
        keyword_id=keyword.id, holdout_percent=study.holdout_percent,
    )
    if digest != assignment.keyword_sha256 or arm != assignment.arm:
        raise TrialAdmissionConflict("trial_assignment_changed")
    return ShadowAdmission(
        study_id=study.study_id, keyword_id=keyword.id,
        arm=arm, trial_version=study.trial_version,
        candidate_digest=study.candidate_source_digest,
        keyword_digest=digest,
        actor_id=actor_id or "system:creative_brief",
    )


def record_shadow_admission(
    session: Session, *, tenant_id: str, admission: ShadowAdmission,
    processing_job: object,
) -> None:
    """Pin a NON-exposure manifest and append same-transaction provenance.

    The reservation stays reserved. This must never be used as verified
    challenger exposure, independent QA or permission to promote policy.
    """
    payload = getattr(processing_job, "payload_json", None)
    if not isinstance(payload, dict):
        raise TrialAdmissionConflict("trial_job_payload_missing")
    if "trial_shadow_admission" in payload:
        raise TrialAdmissionConflict("trial_admission_duplicate")
    manifest = {
        "study_id": admission.study_id,
        "arm": admission.arm,
        "trial_version": admission.trial_version,
        "candidate_source_digest": admission.candidate_digest,
        "keyword_sha256": admission.keyword_digest,
        "mode": "shadow_only",
        "treatment_applied": False,
        "exposure_verified": False,
        "independent_qa_verified": False,
    }
    # SQLAlchemy JSON requires whole-object reassignment to persist changes.
    processing_job.payload_json = {**payload, "trial_shadow_admission": manifest}
    session.add(AuthAuditEventModel(
        tenant_id=tenant_id, actor_id=admission.actor_id,
        action="agent_trial_shadow_admitted",
        detail_json={
            "study_id": admission.study_id,
            "keyword_id": admission.keyword_id,
            "processing_job_id": processing_job.id,
            "arm": admission.arm,
            "trial_version": admission.trial_version,
            "candidate_source_digest": admission.candidate_digest,
            "keyword_sha256": admission.keyword_digest,
            "mode": "shadow_only",
            "exposure_verified": False,
        },
    ))
    session.flush()
