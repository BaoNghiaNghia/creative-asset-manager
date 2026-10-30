"""Read-only Creative Engine V3 P1 bridge: versioned concept metadata from V1.

No provider, Scout, image, worker, DB writes or generated deliverable is created.
V1 descriptive concepts have no validated structural mechanism; never invent one.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Mapping

from app.modules.realistic_review_ugc.creative_brief import validate_brief

SCHEMA_VERSION = "creative-v3.0-preview-v1"
TARGET_PRODUCT = "valucap_8869"
INTENTS = frozenset({
    "typography_first", "humor_first", "identity_first", "emotion_first", "story_first",
})
_MODES = {
    "safe": {"label": "Typography-First", "suggested_mechanism": "NO_TWIST"},
    "creative_twist": {"label": "Creative Twist", "suggested_mechanism": None},
    "explore": {"label": "Explore", "suggested_mechanism": None},
}
_MAX_PREVIEW_TEXT = 160


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_v3_preview(
    *, keyword: str, brief_json: Mapping[str, Any], source_prompt_version: str,
) -> dict[str, Any]:
    """Create stable, conservative V3 preview metadata from a completed V1 brief.

    No creative claims are inferred from arbitrary narrative fields. Meaning,
    mechanism and colorway compatibility still require explicit human review.
    """
    if not isinstance(keyword, str) or not keyword or len(keyword) > 500:
        raise ValueError("invalid source keyword")
    if not isinstance(source_prompt_version, str) or not source_prompt_version.strip():
        raise ValueError("missing source prompt version")
    brief = validate_brief(brief_json, keyword)
    normalized = json.dumps(brief.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    content_hash = _sha(source_prompt_version + "\n" + normalized)
    keyword_hash = _sha(keyword)
    source_strategy = brief.strategy.strip().lower()
    intent_hint = source_strategy if source_strategy in INTENTS else "unclassified"
    not_svg_eligible = len(keyword) > _MAX_PREVIEW_TEXT or any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} or
        (char.isspace() and char != " ") for char in keyword
    )
    concepts = []
    for concept in brief.concepts:
        info = _MODES[concept.mode]
        # Preview fingerprint changes when the V1 source text/brief content changes.
        stable_id = _sha(content_hash + "|" + concept.mode)[:20]
        reasons = [
            "semantic_review_required",
            "design_blueprint_not_composed",
            "embroidery_not_validated",
        ]
        if info["suggested_mechanism"] is None:
            reasons.append("creative_mechanism_unclassified")
        if not_svg_eligible:
            reasons.append("exact_text_svg_preview_not_eligible")
        concepts.append({
            "id": stable_id,
            "source_mode": concept.mode,
            "mode_label": info["label"],
            "name": concept.name,
            "description": concept.description,
            "creative_hook": concept.creative_hook,
            "meaning_rationale": brief.meaning_emotion,
            "familiar_format": concept.familiar_format,
            "twist_description": concept.creative_twist,
            "typography_plan": concept.typography_plan,
            "embroidery_plan": concept.embroidery_plan,
            "clarity_assessment_unverified": concept.clarity_assessment,
            "embroidery_fit_unverified": concept.embroidery_fit,
            "risks": concept.risks,
            "mechanism": info["suggested_mechanism"],
            "mechanism_verified": False,
            "text_spans": [{"text": keyword, "role": "locked_quote"}],
            "design_blueprint_status": "not_composed",
            "quality": {"status": "needs_review", "reasons": reasons},
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "planner_status": "v1_bridge_preview_only",
        "keyword_exact": keyword,
        "keyword_sha256": keyword_hash,
        "source": {
            "brief_prompt_version": source_prompt_version,
            "brief_content_sha256": content_hash,
            "style_dna_id": None,
            "reference_rights_reviewed": False,
        },
        "intent_hint": intent_hint,
        "target_product": TARGET_PRODUCT,
        "placement": "front_crown",
        "mode_policy": {
            "balanced": "creative_twist",
            "explore": "explore",
            "exploit": "requires_explicit_approved_feedback",
        },
        "concepts": concepts,
        "exact_text_locked": True,
        "preview_only": True,
        "imagegen_calls": 0,
        "job_created": False,
        "stage1_output_saved": False,
        "stage2_handoff_performed": False,
        "can_generate_artwork": False,
        "requires_human_review": True,
    }
