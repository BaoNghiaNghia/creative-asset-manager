"""Conservative cross-keyword feedback learning for Stage 1.

Reusable rules are justified by at least three distinct OTHER keyword phrases
and at least six votes for the same semantic cohort. An individual rejection
does not blacklist a keyword or an entire design style. No model calls.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel as Output,
    RrugcKeywordImageJobModel as ImageJob,
)
from app.modules.realistic_review_ugc.stage1_design_learning import REASONS, REASON_GUIDANCE

COHORT_TERMS: dict[str, frozenset[str]] = {
    "music": frozenset({"music", "musician", "musicians", "concert", "guitar", "band", "songs", "song", "live", "jazz", "rock"}),
    "western": frozenset({"cowboy", "cowgirl", "rodeo", "western", "ranch", "horse", "horses"}),
    "pets": frozenset({"dog", "dogs", "cat", "cats", "pet", "pets", "paws", "puppy", "kitten"}),
    "healthcare": frozenset({"nurse", "nurses", "nursing", "icu", "er", "rn", "doctor", "medic"}),
    "coffee": frozenset({"coffee", "espresso", "latte", "caffeine", "barista"}),
    "family": frozenset({"mama", "mom", "mother", "dad", "father", "grandma", "grandpa", "family"}),
    "outdoors": frozenset({"camp", "camping", "hike", "hiking", "fishing", "lake", "mountain", "nature"}),
}
MIN_DISTINCT_KEYWORDS = 3
MIN_REVIEWED_OUTPUTS = 6
MAX_REVIEWED_OUTPUTS = 800


def semantic_cohort(keyword: str) -> str | None:
    tokens = set(re.findall(r"[a-z]+", (keyword or "").casefold()))
    for cohort, terms in COHORT_TERMS.items():
        if tokens.intersection(terms):
            return cohort
    return None


def summarize_cross_keyword_feedback(
    samples: list[tuple[str, dict]],
    *, keyword: str, min_keywords: int = MIN_DISTINCT_KEYWORDS,
    min_votes: int = MIN_REVIEWED_OUTPUTS,
) -> dict:
    cohort = semantic_cohort(keyword)
    empty = {"version": 1, "cohort": cohort, "eligible": False,
             "distinct_keywords": 0, "vote_count": 0, "patterns": []}
    if cohort is None:
        return empty
    reason_words: dict[str, set[str]] = defaultdict(set)
    counts = Counter()
    negative_count = Counter()
    for phrase, metadata in samples[:MAX_REVIEWED_OUTPUTS]:
        if phrase == keyword or semantic_cohort(phrase) != cohort or not isinstance(metadata, dict):
            continue
        review = metadata.get("creative_feedback")
        if not isinstance(review, dict) or review.get("decision") not in ("picked", "rejected"):
            continue
        # One reviewed image gives at most one vote, and only current decisions
        # are read from persisted metadata (Undo does not leave a negative).
        if review["decision"] == "rejected":
            reason = review.get("reason")
            reason = reason if reason in REASONS else "unspecified"
            negative_count[reason] += 1
            reason_words[reason].add(phrase)
        counts[phrase] += 1
    if len(counts) < min_keywords or sum(counts.values()) < min_votes:
        return {**empty, "distinct_keywords": len(counts),
                "vote_count": sum(counts.values())}
    patterns = [{
        "reason": reason, "distinct_keywords": len(reason_words[reason]),
        "rejected_outputs": votes,
        "guidance": REASON_GUIDANCE[reason],
    } for reason, votes in negative_count.most_common()
        if len(reason_words[reason]) >= min_keywords and votes >= 3]
    return {"version": 1, "cohort": cohort, "eligible": True,
            "distinct_keywords": len(counts), "vote_count": sum(counts.values()),
            "patterns": patterns[:5]}


def cross_keyword_learning(session: Session, *, tenant_id: str, keyword: str) -> dict:
    cohort = semantic_cohort(keyword)
    if not tenant_id or cohort is None:
        return summarize_cross_keyword_feedback([], keyword=keyword)
    # A tenant-only, age/row-bounded query; never search other tenants or fetch
    # pixels. Filtering by semantic tokens happens on at most 800 recent rows.
    since = datetime.now(timezone.utc) - timedelta(days=180)
    rows = session.execute(select(ImageJob.keyword_text, Output.font_info_json)
        .join(ImageJob, ImageJob.id == Output.job_id)
        .where(
            Output.tenant_id == tenant_id,
            ImageJob.tenant_id == tenant_id,
            Output.stage == "stage1",
            Output.created_at >= since,
            Output.font_info_json["creative_feedback"]["decision"].as_string().in_(("picked", "rejected")),
        )
        .order_by(Output.created_at.desc())
        .limit(MAX_REVIEWED_OUTPUTS)).all()
    return summarize_cross_keyword_feedback([(phrase, data) for phrase, data in rows],
                                            keyword=keyword)


def cross_keyword_guidance(profile: dict) -> str:
    patterns = profile.get("patterns") if isinstance(profile, dict) else None
    if not isinstance(patterns, list) or not profile.get("eligible"):
        return ""
    if not patterns:
        return ""
    lines = ["CROSS-KEYWORD HUMAN FEEDBACK: advisory patterns for similar phrases "
             "(at least 3 distinct phrases, at least 6 reviews). "
             "Do not blacklist styles or reuse other customers' words."]
    for item in patterns[:5]:
        reason = item.get("reason")
        if reason in REASON_GUIDANCE:
            lines.append(
                f"Human review pattern in {item['distinct_keywords']} other phrases "
                f"({item['rejected_outputs']} rejected images): "
                f"{REASON_GUIDANCE[reason]}"
            )
    lines.append("Keep the exact new phrase and all nine 1/4/4 variants. "
                 "Specific feedback on this exact quote always takes precedence.")
    return "\n".join(lines)
