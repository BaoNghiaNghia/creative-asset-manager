"""Tenant-scoped read-only audit of Stage 1 feedback evidence.

Every preview re-derives the current source vote snapshot and compares it to
the stored immutable candidate. This is NOT a QA/holdout evaluator and NEVER
approves, promotes, dispatches, or mutates an agent, job or database row.

The API must authorize its caller and tenant before reaching this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.keyword_cohort_learning import COHORT_TERMS

from .model import AgentExperienceModel
from .repository import _event_key
from .stage1_feedback import AGENT_ID, SOURCE_VERSION, CandidateSnapshot, snapshots

ReviewStatus = Literal[
    "current_candidate",
    "stale_source",
    "unsupported_source",
    "unexpected_verification",
    "rejected",
    "promoted_requires_review",
    "not_checked",
]

MAX_AUDIT_ROWS = 30
MAX_VERSIONS_PER_AUDIT = 3


@dataclass(frozen=True)
class EvidenceAuditRow:
    event_key: str
    rule_key: str
    agent_version: str
    stored_status: str
    review_status: ReviewStatus
    source_current: bool
    sample_count: int
    distinct_keywords: int | None
    confidence: float
    promotion_allowed: bool = False
    independent_qa_verified: bool = False
    holdout_verified: bool = False

    def public_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "rule_key": self.rule_key,
            "agent_version": self.agent_version,
            "stored_status": self.stored_status,
            "review_status": self.review_status,
            "source_current": self.source_current,
            "sample_count": self.sample_count,
            "distinct_keywords": self.distinct_keywords,
            "confidence": self.confidence,
            "promotion_allowed": False,
            "independent_qa_verified": False,
            "holdout_verified": False,
        }


def _review_row(
    item: AgentExperienceModel,
    current_by_event_key: dict[str, CandidateSnapshot] | None,
) -> EvidenceAuditRow:
    """Use immutable source metadata; do not trust stored evaluation booleans."""
    evidence = item.evaluation_json if isinstance(item.evaluation_json, dict) else {}
    source_is_known = evidence.get("source_kind") == SOURCE_VERSION
    match = current_by_event_key.get(item.event_key) if current_by_event_key is not None else None
    source_current = bool(
        source_is_known
        and match is not None
        and item.rule_key == match.candidate.rule_key
        and item.scope_key == match.candidate.scope_key
        and item.sample_count == match.candidate.sample_count
        and item.confidence == match.candidate.confidence
        and item.source_event_ids_json == list(match.candidate.event_ids)
        and item.evidence_refs_json == list(match.candidate.evidence_refs)
        and item.verification_refs_json == []
    )
    if item.status == "promoted":
        review_status: ReviewStatus = "promoted_requires_review"
    elif item.status == "rejected":
        review_status = "rejected"
    elif not source_is_known:
        review_status = "unsupported_source"
    elif item.verification_refs_json:
        review_status = "unexpected_verification"
    elif current_by_event_key is None:
        review_status = "not_checked"
    elif source_current:
        review_status = "current_candidate"
    else:
        review_status = "stale_source"

    return EvidenceAuditRow(
        event_key=item.event_key,
        rule_key=item.rule_key,
        agent_version=item.agent_version,
        stored_status=item.status,
        review_status=review_status,
        source_current=source_current,
        sample_count=item.sample_count,
        distinct_keywords=match.distinct_keywords if source_current and match else None,
        confidence=item.confidence,
    )


def audit_stage1_candidates(
    session: Session, *,
    tenant_id: str, cohort: str, limit: int = 20,
) -> dict:
    """Bounded current-evidence audit, no write and no raw feedback disclosure."""
    if not tenant_id or cohort not in COHORT_TERMS:
        raise ValueError("invalid tenant or cohort")
    if type(limit) is not int or not 1 <= limit <= MAX_AUDIT_ROWS:
        raise ValueError("invalid audit page size")

    rows = list(session.scalars(
        select(AgentExperienceModel)
        .where(
            AgentExperienceModel.tenant_id == tenant_id,
            AgentExperienceModel.agent_id == AGENT_ID,
            AgentExperienceModel.scope_key == "cohort:" + cohort,
            AgentExperienceModel.status.in_(("candidate", "rejected", "promoted")),
        )
        .order_by(AgentExperienceModel.created_at.desc(), AgentExperienceModel.event_key)
        .limit(limit + 1)
    ).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Avoid repeated scans per row, and bound distinct version scans even
    # if a historical tenant has hundreds of retired agent versions.
    snapshots_by_version: dict[str, dict[str, CandidateSnapshot]] = {}
    audited: list[EvidenceAuditRow] = []
    for item in rows:
        current = snapshots_by_version.get(item.agent_version)
        if current is None and item.agent_version not in snapshots_by_version:
            if len(snapshots_by_version) < MAX_VERSIONS_PER_AUDIT:
                current = {
                    _event_key(summary.candidate): summary
                    for summary in snapshots(
                        session, tenant_id=tenant_id, cohort=cohort,
                        agent_version=item.agent_version,
                    )
                }
                snapshots_by_version[item.agent_version] = current
        audited.append(_review_row(item, current))

    counts = {key: 0 for key in (
        "current_candidate", "stale_source", "unsupported_source",
        "unexpected_verification", "rejected", "promoted_requires_review",
        "not_checked",
    )}
    for item in audited:
        counts[item.review_status] += 1

    return {
        "tenant_id": tenant_id,
        "cohort": cohort,
        "source_version": SOURCE_VERSION,
        "audit_mode": "read_only",
        "result_count": len(audited),
        "has_more": has_more,
        "status_counts": counts,
        "automatic_promotion_enabled": False,
        "agent_execution_enabled": False,
        "independent_qa_verified": False,
        "holdout_verified": False,
        "items": [item.public_dict() for item in audited],
    }
