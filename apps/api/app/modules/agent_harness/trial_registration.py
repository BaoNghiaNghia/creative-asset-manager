"""Phase 3C.2: inert prospective holdout registrations and arm reservations.

Only authenticated, feature-gated API/service callers may use these methods.
Neither study nor reservation changes Stage 1 generation, executes tools,
starts a trial, runs independent QA or promotes Experience candidates.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.keyword_cohort_learning import semantic_cohort
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordCreativeBriefModel,
    RrugcKeywordImageJobModel,
    RrugcKeywordVolumeModel,
)

from .holdout import assign_keyword_arm
from .model import AgentExperienceModel, AgentHoldoutAssignmentModel, AgentHoldoutStudyModel
from .repository import _event_key
from .stage1_feedback import AGENT_ID, SOURCE_VERSION, snapshots
from .trial_schema import HoldoutKeywordReservation, HoldoutStudyRegistration


class HoldoutStudyConflict(ValueError):
    """A requested mutation cannot be safely committed to immutable history."""


def _agent_source_current(
    session: Session, *,
    tenant_id: str, cohort: str, candidate: AgentExperienceModel,
) -> bool:
    if (
        candidate.tenant_id != tenant_id
        or candidate.agent_id != AGENT_ID
        or candidate.scope_key != "cohort:" + cohort
        or candidate.status != "candidate"
        or candidate.verification_refs_json
        or not isinstance(candidate.evaluation_json, dict)
        or candidate.evaluation_json.get("source_kind") != SOURCE_VERSION
    ):
        return False
    for snapshot in snapshots(
        session, tenant_id=tenant_id, cohort=cohort,
        agent_version=candidate.agent_version,
    ):
        proof = snapshot.candidate
        if (
            _event_key(proof) == candidate.event_key
            and candidate.rule_key == proof.rule_key
            and candidate.sample_count == proof.sample_count
            and candidate.confidence == proof.confidence
            and candidate.source_event_ids_json == list(proof.event_ids)
            and candidate.evidence_refs_json == list(proof.evidence_refs)
            and candidate.verification_refs_json == []
        ):
            return True
    return False


def _digest_source(candidate: AgentExperienceModel) -> str:
    raw = json.dumps(
        [
            candidate.tenant_id, candidate.event_key, candidate.agent_id,
            candidate.agent_version, candidate.scope_key, candidate.rule_key,
            candidate.source_event_ids_json, candidate.evidence_refs_json,
            candidate.sample_count,
        ], ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _same_registration(
    stored: AgentHoldoutStudyModel, body: HoldoutStudyRegistration,
) -> bool:
    return (
        stored.cohort == body.cohort
        and stored.candidate_event_key == body.candidate_event_key
        and stored.trial_version == body.trial_version
        and stored.holdout_percent == body.holdout_percent
        and stored.status == "registered"
    )


def register_study(
    session: Session, *, tenant_id: str, body: HoldoutStudyRegistration,
    actor_id: str,
) -> tuple[dict, bool]:
    """No commit: caller supplies authenticated actor and transaction audit."""
    if not tenant_id or not actor_id:
        raise HoldoutStudyConflict("tenant_and_actor_required")
    existing = session.get(AgentHoldoutStudyModel, (tenant_id, body.study_id))
    if existing is not None:
        if not _same_registration(existing, body):
            raise HoldoutStudyConflict("immutable_study_version_conflict")
        return _study_public(existing), False

    candidate = session.get(
        AgentExperienceModel, (tenant_id, body.candidate_event_key),
    )
    if candidate is None or not _agent_source_current(
        session, tenant_id=tenant_id, cohort=body.cohort, candidate=candidate,
    ):
        raise HoldoutStudyConflict("candidate_source_not_current")
    info = {
        "tenant_id": tenant_id, "study_id": body.study_id, "cohort": body.cohort,
        "candidate_event_key": candidate.event_key,
        "agent_version": candidate.agent_version,
        "trial_version": body.trial_version,
        "holdout_percent": body.holdout_percent,
        "status": "registered",
        "active": False,
        "assignment_count": 0,
    }
    if body.dry_run:
        return info, False
    row = AgentHoldoutStudyModel(
        tenant_id=tenant_id, study_id=body.study_id, cohort=body.cohort,
        candidate_event_key=candidate.event_key,
        agent_version=candidate.agent_version,
        trial_version=body.trial_version,
        holdout_percent=body.holdout_percent,
        candidate_source_digest=_digest_source(candidate),
        registered_by=actor_id, status="registered",
    )
    session.add(row)
    session.flush()
    return _study_public(row), True


def _study_public(row: AgentHoldoutStudyModel) -> dict:
    return {
        "tenant_id": row.tenant_id,
        "study_id": row.study_id,
        "cohort": row.cohort,
        "candidate_event_key": row.candidate_event_key,
        "agent_version": row.agent_version,
        "trial_version": row.trial_version,
        "holdout_percent": row.holdout_percent,
        "status": row.status,
        "active": False,
    }


@dataclass(frozen=True)
class ReservationResult:
    items: tuple[dict, ...]
    new_assignments: int

    def summary(self) -> dict:
        counts = Counter(item["arm"] for item in self.items)
        return {
            "reservations": list(self.items),
            "new_assignments": self.new_assignments,
            "control": counts["control"],
            "challenger": counts["challenger"],
            "trial_active": False,
            "production_job_routing_enabled": False,
        }


def reserve_keywords(
    session: Session, *, tenant_id: str, study_id: str,
    body: HoldoutKeywordReservation, actor_id: str,
) -> ReservationResult:
    """Reserve only known tenant-owned keywords without any previous Stage 1 job.

    Locks keyword rows in PostgreSQL; unique tenant+keyword DB constraint guards
    concurrent cross-study collisions. Duplicate reservations are idempotent.
    """
    if not tenant_id or not actor_id:
        raise HoldoutStudyConflict("tenant_and_actor_required")
    study = session.scalar(
        select(AgentHoldoutStudyModel)
        .where(
            AgentHoldoutStudyModel.tenant_id == tenant_id,
            AgentHoldoutStudyModel.study_id == study_id,
        ).with_for_update()
    )
    if study is None or study.status != "registered":
        raise HoldoutStudyConflict("study_not_registered")
    candidate = session.get(
        AgentExperienceModel, (tenant_id, study.candidate_event_key),
    )
    if candidate is None or not _agent_source_current(
        session, tenant_id=tenant_id, cohort=study.cohort, candidate=candidate,
    ) or _digest_source(candidate) != study.candidate_source_digest:
        raise HoldoutStudyConflict("study_candidate_stale")

    keyword_ids = sorted(str(item) for item in body.keyword_ids)
    keywords = list(session.scalars(
        select(RrugcKeywordVolumeModel)
        .where(
            RrugcKeywordVolumeModel.tenant_id == tenant_id,
            RrugcKeywordVolumeModel.id.in_(keyword_ids),
        )
        .order_by(RrugcKeywordVolumeModel.id)
        .with_for_update()
    ).all())
    if len(keywords) != len(keyword_ids):
        raise HoldoutStudyConflict("unknown_tenant_keyword")

    previous = list(session.scalars(
        select(AgentHoldoutAssignmentModel).where(
            AgentHoldoutAssignmentModel.tenant_id == tenant_id,
            AgentHoldoutAssignmentModel.keyword_id.in_(keyword_ids),
        )
    ).all())
    previous_by_id = {item.keyword_id: item for item in previous}
    new_ids = [key for key in keyword_ids if key not in previous_by_id]
    if new_ids:
        # Reserve only before any Creative Brief/job is even queued, not by
        # peeking at candidate/previous output outcomes after the fact.
        brief = session.scalar(
            select(RrugcKeywordCreativeBriefModel.id).where(
                RrugcKeywordCreativeBriefModel.tenant_id == tenant_id,
                RrugcKeywordCreativeBriefModel.keyword_id.in_(new_ids),
            ).limit(1)
        )
        generation_job = session.scalar(
            select(RrugcKeywordImageJobModel.id).where(
                RrugcKeywordImageJobModel.tenant_id == tenant_id,
                RrugcKeywordImageJobModel.keyword_id.in_(new_ids),
            ).limit(1)
        )
        if brief is not None or generation_job is not None:
            raise HoldoutStudyConflict("keyword_generation_already_registered")

    items: list[dict] = []
    for keyword in keywords:
        if semantic_cohort(keyword.keyword) != study.cohort:
            raise HoldoutStudyConflict("keyword_outside_study_cohort")
        if not keyword.keyword_normalized or len(keyword.keyword_normalized) > 500:
            raise HoldoutStudyConflict("invalid_keyword_source")
        digest = hashlib.sha256(
            keyword.keyword_normalized.casefold().strip().encode("utf-8"),
        ).hexdigest()
        arm = assign_keyword_arm(
            tenant_id=tenant_id,
            cohort=study.cohort,
            study_version=f"{study.trial_version}.{study.study_id}",
            keyword_id=keyword.id,
            holdout_percent=study.holdout_percent,
        )
        old = previous_by_id.get(keyword.id)
        if old is not None and (
            old.study_id != study_id or old.arm != arm
            or old.keyword_sha256 != digest or old.status != "reserved"
        ):
            raise HoldoutStudyConflict("keyword_already_assigned_or_modified")
        items.append({
            "keyword_id": keyword.id, "arm": arm,
            "status": "reserved", "persisted": old is not None,
        })
    if not body.dry_run:
        for item in items:
            if item["persisted"]:
                continue
            row = AgentHoldoutAssignmentModel(
                tenant_id=tenant_id, study_id=study_id,
                keyword_id=item["keyword_id"],
                keyword_sha256=hashlib.sha256(
                    next(kw for kw in keywords if kw.id == item["keyword_id"])
                    .keyword_normalized.casefold().strip().encode("utf-8"),
                ).hexdigest(),
                arm=item["arm"], status="reserved", assigned_by=actor_id,
            )
            session.add(row)
        session.flush()
    return ReservationResult(
        tuple(items),
        new_assignments=len(new_ids) if not body.dry_run else 0,
    )
