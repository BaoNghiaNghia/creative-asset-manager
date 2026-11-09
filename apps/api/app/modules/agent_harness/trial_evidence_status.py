"""Safe, tenant-scoped read-only status of Phase 3C.3B evidence ledgers.

Counting database rows NEVER attests reviewer independence, challenger policy
execution, provider cost, sufficient statistical power or promotion readiness.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .model import (
    AgentHoldoutAssignmentModel, AgentHoldoutStudyModel,
    AgentTrialExposureModel, AgentTrialQaReviewModel,
)


def read_stage1_trial_evidence(
    session: Session, *, tenant_id: str, study_id: str,
) -> dict:
    """Caller must first authorize the tenant and pass the read feature gate."""
    study = session.get(AgentHoldoutStudyModel, (tenant_id, study_id))
    if study is None:
        raise LookupError("study_not_found")

    assignments = {"control": {"reserved": 0, "exposed": 0},
                   "challenger": {"reserved": 0, "exposed": 0}}
    for arm, state, count in session.execute(
        select(
            AgentHoldoutAssignmentModel.arm,
            AgentHoldoutAssignmentModel.status,
            func.count(),
        ).where(
            AgentHoldoutAssignmentModel.tenant_id == tenant_id,
            AgentHoldoutAssignmentModel.study_id == study_id,
        ).group_by(
            AgentHoldoutAssignmentModel.arm, AgentHoldoutAssignmentModel.status,
        )
    ):
        if arm in assignments and state in assignments[arm]:
            assignments[arm][state] = int(count)

    receipts = {"control": 0, "challenger": 0}
    for arm, count in session.execute(
        select(AgentTrialExposureModel.arm, func.count()).where(
            AgentTrialExposureModel.tenant_id == tenant_id,
            AgentTrialExposureModel.study_id == study_id,
        ).group_by(AgentTrialExposureModel.arm)
    ):
        if arm in receipts:
            receipts[arm] = int(count)

    # These are raw ledger counts, NOT authenticated independent QA results.
    # Until the trusted reviewer service exists, even a nonzero count cannot
    # justify using this data in quality assessment or auto-promotion.
    qa_record_count = int(session.scalar(
        select(func.count()).select_from(AgentTrialQaReviewModel).where(
            AgentTrialQaReviewModel.tenant_id == tenant_id,
            AgentTrialQaReviewModel.study_id == study_id,
        ),
    ) or 0)

    return {
        "tenant_id": tenant_id,
        "study_id": study_id,
        "cohort": study.cohort,
        "study_status": study.status,
        "assignment_counts": assignments,
        "recorded_exposure_receipts": receipts,
        "qa_rows_unverified": qa_record_count,
        "evaluation_status": "blocked",
        "evaluation_blockers": [
            "challenger_policy_not_applied",
            "independent_qa_provenance_unavailable",
            "trusted_provider_cost_unavailable",
        ],
        "trial_active": False,
        "challenger_treatment_enabled": False,
        "independent_qa_verified": False,
        "automatic_promotion_enabled": False,
    }
