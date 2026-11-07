"""Bounded Stage 1 human feedback -> candidate-only evidence snapshots.

Sources are persisted, tenant-scoped Stage 1 output versions and their parent
jobs. No user text, image pixels, prompt or external API is ever read or sent.
These are NOT verified improvements and can NEVER be promoted here.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.keyword_cohort_learning import (
    COHORT_TERMS, semantic_cohort,
)
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel as Output,
    RrugcKeywordImageJobModel as ImageJob,
)
from app.modules.realistic_review_ugc.stage1_design_learning import REASONS

from .experience import ExperienceCandidate

SOURCE_VERSION = "stage1-human-feedback-evidence-v1"
AGENT_ID = "gatorhats.feedback-intelligence"
MIN_OUTPUTS = 10
MIN_KEYWORDS = 3
MAX_ROWS = 800
MAX_PER_KEYWORD_RULE = 4
MAX_PER_RULE = 40
MAX_CANDIDATES = 5
LOOKBACK_DAYS = 180
_MODES = frozenset({"safe", "creative_twist", "explore"})
_VARIANTS = frozenset({"A", "B", "C", "D"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate: ExperienceCandidate
    distinct_keywords: int

    def summary(self) -> dict:
        return {
            "rule_key": self.candidate.rule_key,
            "sample_count": self.candidate.sample_count,
            "distinct_keywords": self.distinct_keywords,
            "confidence": self.candidate.confidence,
            "status": "candidate",
            "holdout_verified": False,
        }


def snapshots(
    session: Session, *, tenant_id: str, cohort: str, agent_version: str,
) -> list[CandidateSnapshot]:
    """Extract current votes only; validate output/job ownership and asset lineage.

    A single output contributes at most one vote to a rule. A single keyword
    contributes at most 4, and a duplicate artwork hash cannot inflate it.
    Undo/neutral reviews do not appear. Only server-owned enum rules are stored.
    """
    if not tenant_id or cohort not in COHORT_TERMS:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    rows = session.execute(
        select(Output, ImageJob.keyword_text)
        .join(ImageJob, (
            (ImageJob.id == Output.job_id)
            & (ImageJob.tenant_id == Output.tenant_id)
        ))
        .where(
            Output.tenant_id == tenant_id,
            ImageJob.tenant_id == tenant_id,
            Output.stage == "stage1",
            Output.created_at >= cutoff,
            Output.font_info_json["creative_feedback"]["decision"].as_string().in_(
                ("picked", "rejected"),
            ),
        )
        .order_by(Output.created_at.desc(), Output.id.desc())
        .limit(MAX_ROWS)
    ).all()

    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    per_keyword: dict[tuple[str, str], int] = defaultdict(int)
    used_assets: dict[str, set[str]] = defaultdict(set)
    used_outputs: dict[str, set[str]] = defaultdict(set)

    for output, phrase in rows:
        if not isinstance(phrase, str) or semantic_cohort(phrase) != cohort:
            continue
        data = output.font_info_json
        if not isinstance(data, dict) or data.get("technical_qa_passed") is not True:
            continue
        digest = data.get("content_sha256")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            continue
        if not isinstance(output.remote_file_id, str) or not output.remote_file_id.strip():
            continue
        review = data.get("creative_feedback")
        if not isinstance(review, dict):
            continue
        decision = review.get("decision")
        if decision not in ("picked", "rejected"):
            continue
        actor = review.get("updated_by")
        when = review.get("updated_at")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 255:
            continue
        if not isinstance(when, str):
            continue
        try:
            occurred_at = datetime.fromisoformat(when)
        except ValueError:
            continue
        if occurred_at.tzinfo is None or occurred_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            continue

        if decision == "rejected":
            reason = review.get("reason")
            if reason not in REASONS or reason == "unspecified":
                continue
            rule = "stage1:reject:" + reason
        else:
            mode, variant = data.get("concept_mode"), data.get("concept_variant")
            if mode not in _MODES or variant not in _VARIANTS or (mode == "safe" and variant != "A"):
                continue
            rule = f"stage1:pick:{mode}:{variant}"

        key = (rule, phrase.casefold().strip())
        if (per_keyword[key] >= MAX_PER_KEYWORD_RULE
                or len(groups[rule]) >= MAX_PER_RULE
                or digest in used_assets[rule]
                or output.id in used_outputs[rule]):
            continue
        groups[rule].append((phrase.casefold().strip(), output.id, digest))
        used_assets[rule].add(digest)
        used_outputs[rule].add(output.id)
        per_keyword[key] += 1

    eligible = []
    for rule, records in groups.items():
        phrases = {phrase for phrase, _, _ in records}
        if len(records) < MIN_OUTPUTS or len(phrases) < MIN_KEYWORDS:
            continue
        event_ids = tuple(sorted(f"stage1-output:{output_id}" for _, output_id, _ in records))
        evidence_refs = tuple(sorted("sha256:" + digest for _, _, digest in records))
        candidate = ExperienceCandidate(
            tenant_id=tenant_id,
            agent_id=AGENT_ID,
            agent_version=agent_version,
            scope_key="cohort:" + cohort,
            rule_key=rule,
            confidence=0.25,  # human votes are real, cross-keyword outcome isn't verified
            sample_count=len(event_ids),
            event_ids=event_ids,
            evidence_refs=evidence_refs,
            verification_refs=(),  # no independent holdout means no promotion
        )
        eligible.append(CandidateSnapshot(candidate, len(phrases)))
    eligible.sort(key=lambda entry: (-entry.candidate.sample_count, entry.candidate.rule_key))
    return eligible[:MAX_CANDIDATES]
