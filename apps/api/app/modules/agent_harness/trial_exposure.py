"""Phase 3C.3B2A: opt-in, CONTROL-ONLY actual Creative Brief exposure receipt.

A shadow manifest is NEVER an exposure. Only the trusted Stage 1 Codex
worker may call this internal service, AFTER a valid provider result and
inside the same transaction that commits the completed Creative Brief.

Challenger prompt routing is deliberately absent. The presence of an
assignment or a successful baseline job never proves challenger treatment.
No API endpoint, auto-learning, QA verdict or promotion is created here.
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.processing.handlers import ClaimedJob
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel, RrugcKeywordVolumeModel,
)

from .model import (
    AgentExperienceModel, AgentHoldoutAssignmentModel,
    AgentHoldoutStudyModel, AgentTrialExposureModel,
)
from .holdout import assign_keyword_arm
from .trial_admission import admission_enabled
from .trial_registration import _digest_source

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_BRIEF_JOB_TYPE = "rrugc_creative_brief_generate"


class TrialExposureConflict(ValueError):
    """Fail closed without disclosing source keywords or prompts."""


def control_capture_enabled(settings: object) -> bool:
    return bool(
        getattr(settings, "AGENT_HARNESS_TRIAL_CONTROL_CAPTURE_ENABLED", False)
        and admission_enabled(settings)
        and not getattr(settings, "AGENT_HARNESS_EXECUTION_ENABLED", False)
    )


def record_verified_control_exposure(
    session: Session, *, job: ClaimedJob, keyword_id: str,
    policy_version: str, applied_prompt_sha256: str,
    settings: object,
) -> bool:
    """Attest one baseline Codex execution; return True only for a NEW receipt.

    Preconditions are re-checked from the DB under transaction locks. No client
    can call this method via an API. The digest is built by the worker from
    the EXACT prompt arguments that it just used for a successful CLI call.
    Refuse to infer exposures from a shadow manifest or earlier jobs.

    Challenger reservations stay reserved until a separately reviewed worker
    actually applies a different, versioned treatment.
    """
    if not control_capture_enabled(settings):
        return False

    if (
        not job.tenant_id or not keyword_id
        or job.job_type != _BRIEF_JOB_TYPE
        or job.entity_type != "rrugc_keyword_creative_brief"
        or not job.id or not job.entity_id
        or not isinstance(job.lease_owner, str)
        or not 1 <= len(job.lease_owner) <= 96
        or not isinstance(policy_version, str)
        or not 1 <= len(policy_version) <= 32
        or not isinstance(applied_prompt_sha256, str)
        or not _HEX_64.fullmatch(applied_prompt_sha256)
    ):
        raise TrialExposureConflict("trial_worker_provenance_invalid")

    task = session.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.id == job.id,
        ProcessingJobModel.tenant_id == job.tenant_id,
        ProcessingJobModel.job_type == _BRIEF_JOB_TYPE,
        ProcessingJobModel.entity_type == "rrugc_keyword_creative_brief",
        ProcessingJobModel.entity_id == job.entity_id,
    ).with_for_update())
    if (
        task is None or task.status != "processing"
        or task.claimed_by != job.lease_owner
        or task.cancellation_requested
    ):
        raise TrialExposureConflict("trial_processing_lease_invalid")

    stored_payload = task.payload_json
    if not isinstance(stored_payload, dict) or stored_payload.get("keyword_id") != keyword_id:
        raise TrialExposureConflict("trial_job_keyword_mismatch")
    # A stale ClaimedJob snapshot must not be allowed to attest new state.
    if stored_payload != dict(job.payload):
        raise TrialExposureConflict("trial_worker_snapshot_changed")
    manifest = stored_payload.get("trial_shadow_admission")
    if manifest is None:
        # Non-trial jobs do not silently become experiments.
        return False
    if not isinstance(manifest, dict) or manifest.get("mode") != "shadow_only":
        raise TrialExposureConflict("trial_shadow_manifest_invalid")
    if (
        manifest.get("treatment_applied") is not False
        or manifest.get("exposure_verified") is not False
        or manifest.get("independent_qa_verified") is not False
    ):
        raise TrialExposureConflict("trial_shadow_manifest_tampered")

    study_id = manifest.get("study_id")
    if not isinstance(study_id, str) or not 4 <= len(study_id) <= 64:
        raise TrialExposureConflict("trial_study_identity_invalid")

    assignment = session.scalar(select(AgentHoldoutAssignmentModel).where(
        AgentHoldoutAssignmentModel.tenant_id == job.tenant_id,
        AgentHoldoutAssignmentModel.study_id == study_id,
        AgentHoldoutAssignmentModel.keyword_id == keyword_id,
    ).with_for_update())
    if assignment is None or assignment.arm != manifest.get("arm"):
        raise TrialExposureConflict("trial_assignment_mismatch")
    if assignment.arm == "challenger":
        # The current worker still uses the BASELINE policy for both arms.
        # Never count this as challenger treatment or move status to exposed.
        return False
    if assignment.arm != "control" or assignment.status not in ("reserved", "exposed"):
        raise TrialExposureConflict("trial_assignment_invalid")

    study = session.scalar(select(AgentHoldoutStudyModel).where(
        AgentHoldoutStudyModel.tenant_id == job.tenant_id,
        AgentHoldoutStudyModel.study_id == study_id,
    ).with_for_update())
    if (
        study is None or study.status != "registered"
        or study.trial_version != manifest.get("trial_version")
        or study.candidate_source_digest != manifest.get("candidate_source_digest")
    ):
        raise TrialExposureConflict("trial_study_changed")
    expected_arm = assign_keyword_arm(
        tenant_id=job.tenant_id, cohort=study.cohort,
        study_version=f"{study.trial_version}.{study.study_id}",
        keyword_id=keyword_id, holdout_percent=study.holdout_percent,
    )
    if assignment.arm != expected_arm:
        raise TrialExposureConflict("trial_assignment_not_frozen")
    candidate = session.get(
        AgentExperienceModel, (job.tenant_id, study.candidate_event_key),
    )
    if (
        candidate is None or candidate.status != "candidate"
        or candidate.tenant_id != job.tenant_id
        or _digest_source(candidate) != study.candidate_source_digest
    ):
        raise TrialExposureConflict("trial_candidate_changed")

    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.id == keyword_id,
        RrugcKeywordVolumeModel.tenant_id == job.tenant_id,
        RrugcKeywordVolumeModel.picked.is_(True),
    ).with_for_update())
    if keyword is None or not isinstance(keyword.keyword_normalized, str):
        raise TrialExposureConflict("trial_keyword_changed")
    keyword_digest = hashlib.sha256(
        keyword.keyword_normalized.casefold().strip().encode("utf-8"),
    ).hexdigest()
    if (
        keyword_digest != assignment.keyword_sha256
        or keyword_digest != manifest.get("keyword_sha256")
    ):
        raise TrialExposureConflict("trial_keyword_digest_changed")

    brief = session.scalar(select(RrugcKeywordCreativeBriefModel).where(
        RrugcKeywordCreativeBriefModel.id == job.entity_id,
        RrugcKeywordCreativeBriefModel.tenant_id == job.tenant_id,
        RrugcKeywordCreativeBriefModel.keyword_id == keyword_id,
        RrugcKeywordCreativeBriefModel.processing_job_id == job.id,
    ).with_for_update())
    if (
        brief is None or brief.status != "completed"
        or brief.prompt_version != policy_version
        or brief.keyword_sha256 != hashlib.sha256(
            keyword.keyword.encode("utf-8"),
        ).hexdigest()
        or not isinstance(brief.brief_json, dict)
        or brief.brief_json.get("keyword_exact") != keyword.keyword
    ):
        raise TrialExposureConflict("trial_completed_brief_unverified")

    existing = session.get(AgentTrialExposureModel, (job.tenant_id, study_id, keyword_id))
    if existing is not None:
        if (
            existing.arm != "control"
            or existing.processing_job_id != job.id
            or existing.policy_version != policy_version
            or existing.policy_sha256 != applied_prompt_sha256
            or existing.worker_identity != job.lease_owner
            or assignment.status != "exposed"
        ):
            raise TrialExposureConflict("trial_exposure_immutable_conflict")
        return False
    if assignment.status != "reserved":
        raise TrialExposureConflict("trial_assignment_exposed_without_receipt")

    session.add(AgentTrialExposureModel(
        tenant_id=job.tenant_id, study_id=study_id, keyword_id=keyword_id,
        processing_job_id=job.id, arm="control",
        policy_version=policy_version,
        policy_sha256=applied_prompt_sha256,
        worker_identity=job.lease_owner,
    ))
    assignment.status = "exposed"
    session.add(AuthAuditEventModel(
        tenant_id=job.tenant_id, actor_id=f"system:{job.lease_owner}"[:255],
        action="agent_trial_control_exposed",
        detail_json={
            "study_id": study_id, "keyword_id": keyword_id,
            "processing_job_id": job.id,
            "arm": "control", "policy_version": policy_version,
            "policy_sha256": applied_prompt_sha256,
            "source": "trusted_creative_brief_worker",
            "independent_qa_verified": False,
        },
    ))
    session.flush()
    return True
