from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.providers.contracts import AiMetadataAnalysisInput, AiMetadataProvider


PRODUCT_VISUAL_CONTEXT_VERSION = "rrugc-product-visual-context-v1"
PRODUCT_CONTEXT_PROFILE_VERSION = "product-context-v3-feedback-learning"
CONTEXT_FEEDBACK_MIN_MATCHES = 2
CONTEXT_FEEDBACK_PROMOTION_SCORE = 0.20
CONTEXT_FEEDBACK_RANKING_SCALE = 0.08
CONTEXT_FEEDBACK_MAX_RANKING_ADJUSTMENT = 0.06
ProductContextTheme = Literal[
    "pet_owner",
    "dad_family",
    "mom_family",
    "teacher_school",
    "wedding",
    "cycling",
    "sports",
    "outdoor",
    "couple",
    "travel",
    "holiday",
    "memorial",
]


class ProductVisualContextDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    themes: list[ProductContextTheme] = Field(default_factory=list, max_length=6)
    scene_hints: list[str] = Field(default_factory=list, max_length=8)
    audience_hints: list[str] = Field(default_factory=list, max_length=6)
    occasion_hints: list[str] = Field(default_factory=list, max_length=6)
    product_cues: list[str] = Field(default_factory=list, max_length=8)
    avoid_hints: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(max_length=500)

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value: Any) -> Any:
        if isinstance(value, str):
            raw = value.strip().casefold()
            labels = {
                "high": 0.9,
                "medium": 0.6,
                "moderate": 0.6,
                "low": 0.3,
            }
            if raw in labels:
                return labels[raw]
            if raw.endswith("%"):
                try:
                    return float(raw[:-1]) / 100.0
                except ValueError:
                    return value
        return value


_CONTEXT_THEME_RULES: dict[str, tuple[str, ...]] = {
    "pet_owner": (
        "dog",
        "dogs",
        "puppy",
        "cat",
        "cats",
        "kitten",
        "pet",
        "pets",
        "paw",
        "animal portrait",
        "dog portrait",
        "cat portrait",
    ),
    "dad_family": (
        "dad",
        "daddy",
        "father",
        "papa",
        "grandpa",
        "grandfather",
        "family",
    ),
    "mom_family": (
        "mom",
        "mommy",
        "mother",
        "mama",
        "grandma",
        "grandmother",
    ),
    "teacher_school": (
        "teacher",
        "school",
        "classroom",
        "apple",
        "pencil",
        "crayon",
        "ruler",
    ),
    "wedding": (
        "wedding",
        "bride",
        "groom",
        "bridesmaid",
        "best man",
        "marriage",
    ),
    "cycling": (
        "bike",
        "bicycle",
        "cycling",
        "cyclist",
        "biker",
    ),
    "sports": (
        "baseball",
        "football",
        "basketball",
        "soccer",
        "golf",
        "tennis",
        "sport",
        "sports",
        "team",
    ),
    "outdoor": (
        "outdoor",
        "camping",
        "hiking",
        "mountain",
        "trail",
        "beach",
        "lake",
        "fishing",
    ),
    "couple": (
        "couple",
        "husband",
        "wife",
        "boyfriend",
        "girlfriend",
        "anniversary",
    ),
    "travel": (
        "travel",
        "vacation",
        "trip",
        "road trip",
        "adventure",
    ),
    "holiday": (
        "christmas",
        "holiday",
        "xmas",
        "halloween",
        "thanksgiving",
        "valentine",
    ),
    "memorial": (
        "memorial",
        "remember",
        "remembrance",
        "in memory",
        "forever loved",
    ),
}


_CONTEXT_QUERY_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "pet_owner": {
        "direct": (
            "woman with dog candid phone photo",
            "man with dog casual outdoor lifestyle",
            "couple with dog candid natural light",
            "pet owner walking dog smartphone photo",
        ),
        "adjacent": (
            "casual park lifestyle candid phone photo",
            "weekend outdoor lifestyle natural phone photo",
            "home candid lifestyle natural light",
        ),
    },
    "dad_family": {
        "direct": (
            "dad with kids candid phone photo",
            "father son outdoor casual photo",
            "father daughter candid lifestyle",
            "family weekend candid smartphone photo",
        ),
        "adjacent": (
            "backyard family casual natural light",
            "weekend family outdoor candid",
        ),
    },
    "mom_family": {
        "direct": (
            "mom with kids candid phone photo",
            "mother daughter casual lifestyle photo",
            "mother son outdoor candid natural light",
            "family weekend candid smartphone photo",
        ),
        "adjacent": (
            "home family candid natural light",
            "weekend family outdoor phone photo",
        ),
    },
    "teacher_school": {
        "direct": (
            "teacher classroom candid phone photo",
            "teacher school hallway casual photo",
            "teacher desk candid natural light",
        ),
        "adjacent": (
            "woman casual work lifestyle phone photo",
            "campus casual candid natural light",
        ),
    },
    "wedding": {
        "direct": (
            "groom wedding candid phone photo",
            "bride groom candid natural light",
            "wedding party candid smartphone photo",
        ),
        "adjacent": (
            "couple celebration candid natural light",
            "family celebration phone photo",
        ),
    },
    "cycling": {
        "direct": (
            "cyclist casual candid phone photo",
            "man with bicycle outdoor lifestyle",
            "woman with bicycle candid natural light",
            "family bike ride candid smartphone photo",
        ),
        "adjacent": (
            "outdoor active lifestyle phone photo",
            "weekend park casual candid",
        ),
    },
    "sports": {
        "direct": (
            "sports fan candid phone photo",
            "casual game day lifestyle photo",
            "friends at sports field candid",
        ),
        "adjacent": (
            "friends outdoor casual smartphone photo",
            "weekend active lifestyle candid",
        ),
    },
    "outdoor": {
        "direct": (
            "outdoor casual candid phone photo",
            "hiking lifestyle smartphone photo",
            "weekend nature candid natural light",
        ),
        "adjacent": (
            "travel outdoor casual lifestyle",
            "park candid phone photo",
        ),
    },
    "couple": {
        "direct": (
            "couple candid phone photo natural light",
            "couple casual outdoor lifestyle",
            "couple weekend candid smartphone photo",
        ),
        "adjacent": (
            "date day casual candid photo",
            "travel couple natural phone photo",
        ),
    },
    "travel": {
        "direct": (
            "travel candid smartphone photo",
            "vacation casual lifestyle natural light",
            "road trip candid phone photo",
        ),
        "adjacent": (
            "outdoor weekend candid lifestyle",
            "casual city travel phone photo",
        ),
    },
    "holiday": {
        "direct": (
            "family holiday candid phone photo",
            "christmas home candid natural light",
            "holiday couple casual smartphone photo",
        ),
        "adjacent": (
            "home celebration candid photo",
            "family gathering natural phone photo",
        ),
    },
    "memorial": {
        "direct": (
            "sentimental family candid natural light",
            "quiet outdoor portrait phone photo",
        ),
        "adjacent": (
            "meaningful casual portrait natural light",
            "home candid emotional lifestyle photo",
        ),
    },
}


_GENERIC_CONTEXT_QUERIES = (
    "woman casual lifestyle candid phone photo",
    "man casual lifestyle candid phone photo",
    "couple casual outdoor candid smartphone photo",
    "family weekend candid natural light",
)


def _clean_list(values: Iterable[Any] | None, *, limit: int = 12) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = str(raw or "").strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value[:160])
        if len(result) >= limit:
            break
    return result


def _product_context_text(
    product_snapshot: dict[str, Any] | None,
    *,
    campaign_name: str = "",
    queries: Iterable[str] = (),
    notes: str | None = None,
) -> str:
    parts: list[str] = [campaign_name, *queries]
    if notes:
        parts.append(notes)
    if isinstance(product_snapshot, dict):
        for key in (
            "name",
            "sku",
            "product_type",
            "brand",
            "source_category",
            "source_description",
            "color",
            "material",
            "fit_notes",
            "logo_position",
        ):
            value = product_snapshot.get(key)
            if value:
                parts.append(str(value))
        for variant in product_snapshot.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            for key in ("name", "sku", "color"):
                value = variant.get(key)
                if value:
                    parts.append(str(value))
    return " ".join(parts).casefold()


def _detect_themes(text: str) -> list[str]:
    themes: list[str] = []
    for theme, terms in _CONTEXT_THEME_RULES.items():
        if any(term in text for term in terms):
            themes.append(theme)
    if "memorial" in themes and "pet_owner" in themes:
        themes.remove("memorial")
        themes.insert(0, "memorial")
    return themes[:8]


def product_visual_binding_fingerprint(
    product_snapshot: dict[str, Any] | None,
    reference_snapshot: Iterable[dict[str, Any]] | None,
) -> str:
    references = []
    for item in reference_snapshot or ():
        if not isinstance(item, dict):
            continue
        references.append({
            "id": str(item.get("id") or ""),
            "variant_id": str(item.get("variant_id") or ""),
            "view_type": str(item.get("view_type") or ""),
            "version": int(item.get("version") or 0),
            "content_hash": str(item.get("content_hash") or ""),
        })
    payload = {
        "product_id": str((product_snapshot or {}).get("id") or ""),
        "product_revision": int((product_snapshot or {}).get("revision") or 0),
        "references": sorted(
            references,
            key=lambda row: (
                row["variant_id"],
                row["view_type"],
                row["id"],
            ),
        ),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def derive_context_feedback_learning(
    rows: Iterable[tuple[str, str] | tuple[str, str, str | None]] = (),
) -> dict[str, Any]:
    query_stats: dict[str, dict[str, Any]] = {}
    total_good = 0
    total_wrong = 0
    for row in rows:
        if len(row) < 2:
            continue
        query = str(row[0] or "").strip()
        label = str(row[1] or "").strip().casefold()
        if not query or label not in {"good", "wrong"}:
            continue
        key = query.casefold()
        stat = query_stats.setdefault(
            key,
            {"query": query[:160], "good": 0, "wrong": 0},
        )
        if label == "good":
            stat["good"] += 1
            total_good += 1
        else:
            stat["wrong"] += 1
            total_wrong += 1

    scored: list[dict[str, Any]] = []
    for stat in query_stats.values():
        good = int(stat["good"])
        wrong = int(stat["wrong"])
        reviews = good + wrong
        if not reviews:
            continue
        raw_signal = (good - wrong) / reviews
        shrinkage = reviews / (reviews + 3.0)
        score = round(raw_signal * shrinkage, 4)
        scored.append({
            "query": stat["query"],
            "good": good,
            "wrong": wrong,
            "reviews": reviews,
            "score": score,
        })

    scored.sort(
        key=lambda item: (
            item["reviews"],
            abs(item["score"]),
            item["query"].casefold(),
        ),
        reverse=True,
    )
    promoted = [
        item["query"]
        for item in scored
        if item["reviews"] >= CONTEXT_FEEDBACK_MIN_MATCHES
        and item["good"] >= CONTEXT_FEEDBACK_MIN_MATCHES
        and item["score"] >= CONTEXT_FEEDBACK_PROMOTION_SCORE
    ][:8]
    suppressed = [
        item["query"]
        for item in scored
        if item["reviews"] >= CONTEXT_FEEDBACK_MIN_MATCHES
        and item["wrong"] >= CONTEXT_FEEDBACK_MIN_MATCHES
        and item["score"] <= -CONTEXT_FEEDBACK_PROMOTION_SCORE
    ][:8]
    total_reviews = total_good + total_wrong
    return {
        "active": bool(promoted or suppressed),
        "minimum_consistent_reviews": CONTEXT_FEEDBACK_MIN_MATCHES,
        "promotion_score_threshold": CONTEXT_FEEDBACK_PROMOTION_SCORE,
        "total_reviews": total_reviews,
        "good_count": total_good,
        "wrong_count": total_wrong,
        "promoted_queries": promoted,
        "suppressed_queries": suppressed,
        "query_scores": scored[:12],
    }


def context_feedback_ranking_signal(
    *,
    profile: dict[str, Any] | None,
    source_query: str | None,
    base_score: float | None,
) -> dict[str, Any]:
    query = str(source_query or "").strip()
    ranking_score = (
        round(max(0.0, min(1.0, float(base_score))), 4)
        if base_score is not None
        else None
    )
    result = {
        "active": False,
        "source_query": query or None,
        "direction": None,
        "learned_score": 0.0,
        "adjustment": 0.0,
        "ranking_score": ranking_score,
        "reviews": 0,
        "good": 0,
        "wrong": 0,
    }
    if not query or not isinstance(profile, dict):
        return result

    learning = (
        dict(profile.get("feedback_learning"))
        if isinstance(profile.get("feedback_learning"), dict)
        else {}
    )
    if not learning.get("active"):
        return result

    key = query.casefold()
    promoted_keys = {
        str(value or "").strip().casefold()
        for value in learning.get("promoted_queries") or []
        if str(value or "").strip()
    }
    suppressed_keys = {
        str(value or "").strip().casefold()
        for value in learning.get("suppressed_queries") or []
        if str(value or "").strip()
    }
    if key not in promoted_keys and key not in suppressed_keys:
        return result

    matched: dict[str, Any] | None = None
    for row in learning.get("query_scores") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("query") or "").strip().casefold() == key:
            matched = row
            break
    if matched is None:
        return result

    learned_score = float(matched.get("score") or 0.0)
    adjustment = max(
        -CONTEXT_FEEDBACK_MAX_RANKING_ADJUSTMENT,
        min(
            CONTEXT_FEEDBACK_MAX_RANKING_ADJUSTMENT,
            learned_score * CONTEXT_FEEDBACK_RANKING_SCALE,
        ),
    )
    if key in promoted_keys:
        adjustment = abs(adjustment)
        direction = "boost"
    else:
        adjustment = -abs(adjustment)
        direction = "downrank"

    ranking_score = (
        round(max(0.0, min(1.0, float(base_score) + adjustment)), 4)
        if base_score is not None
        else None
    )
    return {
        "active": bool(adjustment),
        "source_query": query,
        "direction": direction,
        "learned_score": round(learned_score, 4),
        "adjustment": round(adjustment, 4),
        "ranking_score": ranking_score,
        "reviews": int(matched.get("reviews") or 0),
        "good": int(matched.get("good") or 0),
        "wrong": int(matched.get("wrong") or 0),
    }


def derive_product_context_profile(
    *,
    product_snapshot: dict[str, Any] | None,
    campaign_name: str = "",
    queries: Iterable[str] = (),
    config: dict[str, Any] | None = None,
    reference_snapshot: Iterable[dict[str, Any]] | None = None,
    context_feedback: Iterable[
        tuple[str, str] | tuple[str, str, str | None]
    ] = (),
) -> dict[str, Any]:
    raw = dict(config or {})
    auto_context = bool(raw.get("auto_context", True))
    operator_themes = _clean_list(
        raw.get("operator_themes")
        if raw.get("operator_themes") is not None
        else raw.get("themes"),
        limit=8,
    )
    preferred_scenes = _clean_list(raw.get("preferred_scenes"), limit=8)
    avoid = _clean_list(raw.get("avoid"), limit=8)
    notes = str(raw.get("notes") or "").strip()[:1000] or None

    raw_visual = (
        dict(raw.get("visual_context"))
        if isinstance(raw.get("visual_context"), dict)
        else {}
    )
    current_visual_fingerprint = product_visual_binding_fingerprint(
        product_snapshot,
        reference_snapshot,
    )
    visual_status = str(raw_visual.get("status") or "not_analyzed")
    stored_visual_fingerprint = str(raw_visual.get("binding_fingerprint") or "")
    if (
        visual_status == "ready"
        and stored_visual_fingerprint
        and stored_visual_fingerprint != current_visual_fingerprint
    ):
        visual_status = "stale"
    visual_context = {
        **raw_visual,
        "status": visual_status,
    }
    visual_ready = auto_context and visual_status == "ready"
    visual_themes = (
        _clean_list(raw_visual.get("themes"), limit=6)
        if visual_ready
        else []
    )
    visual_scene_hints = (
        _clean_list(raw_visual.get("scene_hints"), limit=8)
        if visual_ready
        else []
    )

    text = _product_context_text(
        product_snapshot,
        campaign_name=campaign_name,
        queries=queries,
        notes=notes,
    )
    text_themes = _detect_themes(text) if auto_context else []
    detected_themes = _clean_list(
        [*visual_themes, *text_themes],
        limit=8,
    )
    themes = _clean_list([*operator_themes, *detected_themes], limit=8)
    feedback_learning = derive_context_feedback_learning(context_feedback)
    promoted_queries = _clean_list(
        feedback_learning.get("promoted_queries"),
        limit=8,
    )
    suppressed_query_keys = {
        value.casefold()
        for value in _clean_list(
            feedback_learning.get("suppressed_queries"),
            limit=8,
        )
    }

    # Visible design evidence is the strongest signal for source-image discovery.
    # Put scene concepts inferred from the actual embroidery/artwork ahead of
    # broad theme templates so a design like "Best Grandpa By Par" searches
    # for a grandfather golfing context before generic family/sports scenes.
    direct: list[str] = [
        f"{scene} candid phone photo"
        for scene in [*visual_scene_hints, *preferred_scenes]
    ]
    adjacent: list[str] = []
    for theme in themes:
        templates = _CONTEXT_QUERY_TEMPLATES.get(theme, {})
        direct.extend(templates.get("direct", ()))
        adjacent.extend(templates.get("adjacent", ()))

    # Human context feedback only changes discovery after at least two
    # consistent reviews for the same search context. Promoted queries are
    # added as direct targets; suppressed queries are removed from derived
    # clusters but manual anchor queries remain protected by the caller.
    direct = [*promoted_queries, *direct]
    search_clusters = {
        "direct": [
            value
            for value in _clean_list(direct, limit=20)
            if value.casefold() not in suppressed_query_keys
        ][:16],
        "adjacent": [
            value
            for value in _clean_list(adjacent, limit=16)
            if value.casefold() not in suppressed_query_keys
        ][:12],
        "generic": [
            value
            for value in _GENERIC_CONTEXT_QUERIES
            if value.casefold() not in suppressed_query_keys
        ],
    }

    product_name = None
    product_type = None
    if isinstance(product_snapshot, dict):
        product_name = str(product_snapshot.get("name") or "").strip() or None
        product_type = str(product_snapshot.get("product_type") or "").strip() or None

    return {
        "auto_context": auto_context,
        "operator_themes": operator_themes,
        "detected_themes": detected_themes,
        "themes": themes,
        "preferred_scenes": preferred_scenes,
        "avoid": avoid,
        "notes": notes,
        "visual_context": visual_context,
        "feedback_learning": feedback_learning,
        "search_clusters": search_clusters,
        "source": {
            "product_name": product_name,
            "product_type": product_type,
            "has_product_snapshot": isinstance(product_snapshot, dict),
            "visual_context_status": visual_status,
            "visual_reference_count": int(raw_visual.get("references_analyzed") or 0),
        },
        "version": PRODUCT_CONTEXT_PROFILE_VERSION,
    }


def product_context_search_queries(profile: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not isinstance(profile, dict):
        return []
    clusters = profile.get("search_clusters")
    if not isinstance(clusters, dict):
        return []
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for level in ("direct", "adjacent", "generic"):
        values = clusters.get(level)
        if not isinstance(values, list):
            continue
        for raw in values:
            value = str(raw or "").strip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            rows.append((value, level))
    return rows



def product_visual_context_prompt(
    product_snapshot: dict[str, Any] | None,
    *,
    view_type: str,
) -> str:
    product = dict(product_snapshot or {})
    details = []
    for label, key in (
        ("name", "name"),
        ("type", "product_type"),
        ("brand", "brand"),
        ("category", "source_category"),
        ("description", "source_description"),
        ("material", "material"),
        ("color", "color"),
        ("fit notes", "fit_notes"),
    ):
        value = str(product.get(key) or "").strip()
        if value:
            details.append(f"- {label}: {value[:700]}")
    product_block = "\n".join(details) or "- no reliable text metadata"
    theme_ids = ", ".join(_CONTEXT_THEME_RULES)
    return f"""
You are analyzing one product reference image to improve Pinterest lifestyle-reference discovery.
Return exactly one JSON object matching the supplied schema and no prose.

The image is a product/reference view, not a lifestyle-person reference.
Use visible artwork, embroidery, icons, words, motifs, product construction and personalization cues
to infer plausible usage context. Product metadata is supporting evidence, but visible image evidence
should correct vague or incomplete metadata.

Do not identify any real person. Do not infer protected traits or sensitive demographic attributes.
Do not invent a theme when the product does not visibly or textually support it.

Reference view: {view_type}

PRODUCT METADATA:
{product_block}

Allowed theme ids:
{theme_ids}

Guidance:
- themes: only allowed ids with meaningful visual/text evidence.
- scene_hints: short Pinterest-search scene concepts, 2-6 words each, such as
  "dog owner park", "dad bike ride", "teacher classroom", "couple weekend outing".
  Describe likely lifestyle context, not studio product photography.
- audience_hints: neutral relationship/role labels supported by the design, e.g. dog owner, dad,
  teacher, couple. Do not infer age, ethnicity, religion, health, or other protected/sensitive traits.
- occasion_hints: gift/use occasions visibly or textually supported by the product.
- product_cues: concise visible evidence that drove the inference.
- avoid_hints: contexts that would clearly conflict with visible product meaning; keep empty when unsure.
- confidence: confidence in the contextual inference, not image quality.
- summary: one factual sentence, max 500 characters.
""".strip()


async def analyze_product_visual_reference(
    *,
    provider: AiMetadataProvider,
    tenant_id: str,
    reference_id: str,
    image_bytes: bytes,
    image_mime_type: str,
    width: int | None,
    height: int | None,
    product_snapshot: dict[str, Any] | None,
    view_type: str,
) -> tuple[ProductVisualContextDocument, str, str | None]:
    result = await provider.analyze_single(
        AiMetadataAnalysisInput(
            tenant_id=tenant_id,
            asset_id=reference_id,
            prompt=product_visual_context_prompt(
                product_snapshot,
                view_type=view_type,
            ),
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
            metadata_profile="rrugc_product_context_visual",
            metadata_profile_version=PRODUCT_VISUAL_CONTEXT_VERSION,
            image_width=width,
            image_height=height,
            json_schema=ProductVisualContextDocument.model_json_schema(),
            analysis_id=reference_id + ":product-context",
        )
    )
    document = ProductVisualContextDocument.model_validate(dict(result.metadata))
    return document, result.provider, result.model


def merge_product_visual_context(
    analyses: Iterable[
        tuple[
            ProductVisualContextDocument,
            dict[str, Any],
            str,
            str | None,
        ]
    ],
    *,
    binding_fingerprint: str,
    analyzed_at: str,
) -> dict[str, Any]:
    rows = list(analyses)
    if not rows:
        return {
            "status": "not_analyzed",
            "themes": [],
            "scene_hints": [],
            "audience_hints": [],
            "occasion_hints": [],
            "product_cues": [],
            "avoid_hints": [],
            "confidence": 0.0,
            "summary": None,
            "references_analyzed": 0,
            "reference_ids": [],
            "reference_views": [],
            "binding_fingerprint": binding_fingerprint,
            "analyzed_at": analyzed_at,
            "version": PRODUCT_VISUAL_CONTEXT_VERSION,
        }

    theme_scores: dict[str, float] = {}
    scene_rows: list[str] = []
    audience_rows: list[str] = []
    occasion_rows: list[str] = []
    cue_rows: list[str] = []
    avoid_rows: list[str] = []
    summaries: list[tuple[float, str]] = []
    reference_ids: list[str] = []
    reference_views: list[str] = []
    providers: list[str] = []
    models: list[str] = []
    confidence_total = 0.0

    for document, reference, provider_name, model in rows:
        confidence = float(document.confidence)
        confidence_total += confidence
        for theme in document.themes:
            theme_scores[theme] = theme_scores.get(theme, 0.0) + max(0.15, confidence)
        scene_rows.extend(document.scene_hints)
        audience_rows.extend(document.audience_hints)
        occasion_rows.extend(document.occasion_hints)
        cue_rows.extend(document.product_cues)
        avoid_rows.extend(document.avoid_hints)
        summary = document.summary.strip()
        if summary:
            summaries.append((confidence, summary))
        reference_id = str(reference.get("id") or "").strip()
        if reference_id:
            reference_ids.append(reference_id)
        view_type = str(reference.get("view_type") or "").strip()
        if view_type:
            reference_views.append(view_type)
        if provider_name:
            providers.append(provider_name)
        if model:
            models.append(model)

    ranked_themes = [
        theme
        for theme, _score in sorted(
            theme_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ][:6]
    summaries.sort(key=lambda item: -item[0])

    return {
        "status": "ready",
        "themes": ranked_themes,
        "scene_hints": _clean_list(scene_rows, limit=8),
        "audience_hints": _clean_list(audience_rows, limit=6),
        "occasion_hints": _clean_list(occasion_rows, limit=6),
        "product_cues": _clean_list(cue_rows, limit=10),
        "avoid_hints": _clean_list(avoid_rows, limit=6),
        "confidence": round(confidence_total / len(rows), 4),
        "summary": summaries[0][1][:500] if summaries else None,
        "references_analyzed": len(rows),
        "reference_ids": _clean_list(reference_ids, limit=12),
        "reference_views": _clean_list(reference_views, limit=12),
        "providers": _clean_list(providers, limit=4),
        "models": _clean_list(models, limit=4),
        "binding_fingerprint": binding_fingerprint,
        "analyzed_at": analyzed_at,
        "version": PRODUCT_VISUAL_CONTEXT_VERSION,
    }


def select_product_visual_references(
    reference_snapshot: Iterable[dict[str, Any]] | None,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    rows = [
        dict(item)
        for item in (reference_snapshot or ())
        if isinstance(item, dict) and item.get("remote_file_id")
    ]
    view_priority = {
        "embroidery_closeup": 0,
        "logo_closeup": 1,
        "front": 2,
        "material_closeup": 3,
        "front_45_left": 4,
        "front_45_right": 5,
        "side_left": 6,
        "side_right": 7,
        "back": 8,
        "top": 9,
    }
    rows.sort(
        key=lambda item: (
            view_priority.get(str(item.get("view_type") or ""), 99),
            0 if item.get("variant_id") is None else 1,
            str(item.get("variant_id") or ""),
            str(item.get("id") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(item: dict[str, Any] | None) -> None:
        if item is None or len(selected) >= limit:
            return
        item_id = str(item.get("id") or "")
        if not item_id or item_id in selected_ids:
            return
        selected.append(item)
        selected_ids.add(item_id)

    # Detail views reveal personalized artwork/text that generic front views can miss.
    for view_type in ("embroidery_closeup", "logo_closeup"):
        add(next(
            (
                item
                for item in rows
                if str(item.get("view_type") or "") == view_type
            ),
            None,
        ))

    # Keep the overall base product silhouette even when detail views use the
    # same base/variant id; those are complementary evidence, not duplicates.
    add(next(
        (
            item
            for item in rows
            if str(item.get("view_type") or "") == "front"
            and item.get("variant_id") is None
        ),
        None,
    ))

    # Then spread the remaining budget across distinct color variants.
    seen_front_variants: set[str] = set()
    for item in rows:
        if len(selected) >= limit:
            break
        if str(item.get("view_type") or "") != "front":
            continue
        variant_id = str(item.get("variant_id") or "")
        if not variant_id or variant_id in seen_front_variants:
            continue
        add(item)
        seen_front_variants.add(variant_id)

    # Fill any spare slots with the next most informative views.
    for item in rows:
        if len(selected) >= limit:
            break
        add(item)
    return selected
