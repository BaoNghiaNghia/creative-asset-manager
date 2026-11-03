"""Durable tenant-scoped Stage 1 design feedback and bounded, cost-free learning.

Uses the existing per-output JSON metadata to avoid production schema changes.
Only a human review decision is authoritative. Structural learning is advisory:
no generated image is claimed visually identical without pixel/perceptual QA.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordImageJobModel,
)

DECISIONS = frozenset({"picked", "rejected", "neutral"})
REASONS = frozenset({
    "unspecified", "unreadable_text", "wrong_spelling", "poor_cap_fit",
    "generic_layout", "too_busy", "wrong_style", "duplicate", "poor_embroidery",
})
MODE_LABELS = {
    "safe": "Typography-first",
    "creative_twist": "Creative Twist",
    "explore": "Explore",
}
REASON_GUIDANCE = {
    "unspecified": "Avoid recreating the previously rejected letter construction and layout.",
    "unreadable_text": "Make the complete quote immediately readable, with open letter counters.",
    "wrong_spelling": "Preserve every original letter, punctuation mark and word in order.",
    "poor_cap_fit": "Use a balanced compact composition fitting a curved cap front.",
    "generic_layout": "Use a new, non-generic word hierarchy and memorable original letterforms.",
    "too_busy": "Remove unnecessary icons, ornament, collisions and visual clutter.",
    "wrong_style": "Change the overall style DNA and typographic character.",
    "duplicate": "Change the primary word silhouette, hierarchy and layout substantially.",
    "poor_embroidery": "Use robust satin-readable strokes and larger open negative spaces.",
}


def set_design_feedback(
    row: RrugcImageOutputVersionModel, *, decision: str,
    reason: str, actor_id: str,
) -> dict:
    if row.stage != "stage1":
        raise ValueError("Feedback is limited to Stage 1 artwork")
    if decision not in DECISIONS or reason not in REASONS:
        raise ValueError("Invalid design feedback decision/reason")
    if decision != "rejected":
        reason = "unspecified"
    metadata = dict(row.font_info_json or {})
    review = {
        "decision": decision, "reason": reason,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": actor_id,
    }
    metadata["creative_feedback"] = review
    row.font_info_json = metadata
    return review


def learning_profile(session: Session, *, tenant_id: str, keyword: str) -> dict:
    """Learn independently per tenant and exact quote; never blacklist a broad style.

    A single reject is enough to avoid reusing that variant's old geometry for
    this quote, but it does not ban that mode for unrelated phrases.
    """
    if not tenant_id or not keyword:
        return {"version": 1, "rejected": [], "picked": [],
                "general_rejections": [], "rejected_hashes": [],
                "rejected_visual_hashes": [], "total_votes": 0}
    rows = session.execute(select(
        RrugcImageOutputVersionModel.font_info_json,
    ).join(RrugcKeywordImageJobModel,
           RrugcKeywordImageJobModel.id == RrugcImageOutputVersionModel.job_id).where(
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcKeywordImageJobModel.tenant_id == tenant_id,
        RrugcKeywordImageJobModel.keyword_text == keyword,
        RrugcImageOutputVersionModel.stage == "stage1",
        RrugcImageOutputVersionModel.font_info_json["creative_feedback"]["decision"].as_string().in_(("picked", "rejected")),
    ).order_by(RrugcImageOutputVersionModel.created_at.desc()).limit(400)).scalars().all()
    signals: dict[tuple[str, str], Counter] = {}
    votes = 0
    blocked_hashes: set[str] = set()
    blocked_visual: set[str] = set()
    general_reasons: Counter = Counter()
    for raw in rows:
        metadata = raw if isinstance(raw, dict) else {}
        review = metadata.get("creative_feedback")
        if not isinstance(review, dict) or review.get("decision") not in {"picked", "rejected"}:
            continue
        if review["decision"] == "rejected":
            digest = metadata.get("content_sha256")
            if isinstance(digest, str) and len(digest) == 64 and all(
                ch in "0123456789abcdef" for ch in digest
            ):
                blocked_hashes.add(digest)
            from app.modules.realistic_review_ugc.typography_similarity import is_typography_dhash
            visual = metadata.get("stage1_visual_dhash")
            if is_typography_dhash(visual):
                blocked_visual.add(visual)
        mode, variant = metadata.get("concept_mode"), metadata.get("concept_variant")
        if mode not in MODE_LABELS or variant not in {"A", "B", "C", "D"} or (
            mode == "safe" and variant != "A"
        ):
            if review["decision"] == "rejected":
                reason = review.get("reason")
                general_reasons[reason if reason in REASONS else "unspecified"] += 1
            votes += 1
            continue
        key = (mode, variant)
        signals.setdefault(key, Counter())
        signals[key][review["decision"]] += 1
        if review["decision"] == "rejected":
            reason = review.get("reason")
            signals[key]["reason:" + (reason if reason in REASONS else "unspecified")] += 1
        votes += 1
    rejects = []
    picked = []
    for (mode, variant), counts in sorted(signals.items()):
        item = {"mode": mode, "variant": variant, "votes": sum(
            counts[action] for action in ("picked", "rejected"))}
        if counts["rejected"]:
            reason = max(REASONS, key=lambda r: (counts["reason:" + r], r))
            rejects.append({
                **item, "count": counts["rejected"], "reason": reason,
                "guidance": REASON_GUIDANCE[reason],
                "strong_negative": counts["rejected"] > counts["picked"],
            })
        if counts["picked"]:
            picked.append({**item, "count": counts["picked"]})
    general = [
        {"reason": reason, "count": count, "guidance": REASON_GUIDANCE[reason]}
        for reason, count in general_reasons.most_common(5)
    ]
    return {"version": 1, "rejected": rejects, "picked": picked,
            "general_rejections": general,
            "rejected_hashes": sorted(blocked_hashes),
            "rejected_visual_hashes": sorted(blocked_visual),
            "total_votes": votes}


def learning_prompt_context(profile: dict) -> str:
    """Only controlled enums and fixed system-written guidance are forwarded."""
    negatives = profile["rejected"]
    positives = profile["picked"]
    general = profile.get("general_rejections", [])
    if not negatives and not positives and not general:
        return ""
    parts = ["HUMAN DESIGN FEEDBACK (verified decisions for this exact phrase):"]
    for item in negatives[:9]:
        parts.append(
            f"REJECTED {MODE_LABELS[item['mode']]} {item['variant']} "
            f"({item['count']} negative vote(s)): {item['guidance']} "
            "Do not recreate this rejected composition; redesign its geometry."
        )
    for item in general[:5]:
        parts.append(
            f"REJECTED ARTWORK with unclassified style ({item['count']} vote(s)): "
            f"{item['guidance']} Do not repeat the rejected visual approach."
        )
    for item in positives[:9]:
        parts.append(
            f"PICKED {MODE_LABELS[item['mode']]} {item['variant']} "
            f"({item['count']} vote(s)): preserve only general readability and "
            "buyer relevance, not an exact duplicate."
        )
    parts.append("Still produce nine DIFFERENT designs (1/4/4). "
                 "These are layout-specific preferences, not bans on whole modes. "
                 "Never copy rejected designs or replace the exact quote.")
    return "\n".join(parts)


def learning_fingerprint(profile: dict) -> str:
    stable = {
        "rejected": profile["rejected"], "picked": profile["picked"],
        "total_votes": profile["total_votes"],
        "rejected_hashes": profile.get("rejected_hashes", []),
        "rejected_visual_hashes": profile.get("rejected_visual_hashes", []),
        "general_rejections": profile.get("general_rejections", []),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
