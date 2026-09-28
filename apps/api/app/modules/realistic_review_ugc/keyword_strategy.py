from __future__ import annotations

import re
from typing import Any, Iterable


MAX_CAMPAIGN_SEARCH_QUERIES = 10

_HEADWEAR_TERMS = (
    "hat",
    "hats",
    "cap",
    "caps",
    "baseball cap",
    "bucket hat",
    "beanie",
    "sun hat",
    "straw hat",
    "cowboy hat",
    "dad hat",
    "trucker hat",
    "headwear",
    "mũ",
    "đội mũ",
)

_NEGATIVE_DISCOVERY_TERMS = (
    "editorial",
    "runway",
    "fashion shoot",
    "fashion photography",
    "studio portrait",
    "studio shoot",
    "product only",
    "product photography",
    "flat lay",
    "flatlay",
    "mannequin",
    "catalog",
    "cutout",
    "isolated",
    "illustration",
    "sketch",
)

_REALISM_TERMS = (
    "candid",
    "casual",
    "lifestyle",
    "phone photo",
    "smartphone",
    "natural light",
    "selfie",
    "outdoor",
    "travel",
    "home",
)

_PERSONA_TERMS: dict[str, tuple[str, ...]] = {
    "man": ("man", "men", "male", "guy", "dad", "father", "nam", "đàn ông"),
    "woman": ("woman", "women", "female", "girl", "mom", "mother", "nữ", "phụ nữ"),
    "couple": ("couple", "boyfriend", "girlfriend", "cặp đôi"),
    "family": ("family", "parents", "parent", "gia đình"),
    "friends": ("friends", "friend group", "group of friends", "bạn bè"),
}

_GENERIC_PERSON_TERMS = (
    "person",
    "people",
    "human",
    "người",
)

_SPECIFIC_HAT_TYPES = (
    "baseball cap",
    "bucket hat",
    "beanie",
    "sun hat",
    "straw hat",
    "cowboy hat",
    "dad hat",
    "trucker hat",
)


def _contains_term(text: str, term: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text.casefold()) is not None


def _combined_text(
    *,
    name: str = "",
    queries: Iterable[str] = (),
    product_snapshot: dict[str, Any] | None = None,
) -> str:
    parts = [name, *queries]
    if isinstance(product_snapshot, dict):
        for key in ("name", "product_type", "fit_notes", "brim_style", "crown_profile"):
            value = product_snapshot.get(key)
            if value:
                parts.append(str(value))
    return " ".join(str(part or "") for part in parts).casefold()


def detect_campaign_keyword_intent(
    *,
    name: str = "",
    queries: Iterable[str] = (),
    product_snapshot: dict[str, Any] | None = None,
) -> str | None:
    text = _combined_text(
        name=name,
        queries=queries,
        product_snapshot=product_snapshot,
    )
    if any(_contains_term(text, term) for term in _HEADWEAR_TERMS):
        return "hat_people"
    return None


def _detected_personas(text: str) -> list[str]:
    detected = [
        persona
        for persona, terms in _PERSONA_TERMS.items()
        if any(_contains_term(text, term) for term in terms)
    ]
    if detected:
        return detected
    if any(_contains_term(text, term) for term in _GENERIC_PERSON_TERMS):
        return ["man", "woman", "couple", "family", "friends"]
    return ["man", "woman", "couple", "family", "friends"]


def _hat_type(text: str) -> str | None:
    for hat_type in _SPECIFIC_HAT_TYPES:
        if _contains_term(text, hat_type):
            return hat_type
    return None


def query_is_suppressed_for_reference_search(query: str) -> bool:
    text = query.casefold()
    return any(_contains_term(text, term) for term in _NEGATIVE_DISCOVERY_TERMS)


def _existing_query_is_worth_preserving(query: str) -> bool:
    if query_is_suppressed_for_reference_search(query):
        return False
    text = query.casefold()
    has_headwear = any(_contains_term(text, term) for term in _HEADWEAR_TERMS)
    has_persona = (
        any(_contains_term(text, term) for terms in _PERSONA_TERMS.values() for term in terms)
        or any(_contains_term(text, term) for term in _GENERIC_PERSON_TERMS)
    )
    has_realism = any(_contains_term(text, term) for term in _REALISM_TERMS)
    return has_headwear and has_persona and has_realism


def _persona_templates(persona: str, explicit_hat_type: str | None) -> list[str]:
    primary_hat = explicit_hat_type or {
        "man": "baseball cap",
        "woman": "bucket hat",
        "couple": "hats",
        "family": "hats",
        "friends": "caps",
    }[persona]
    if persona == "man":
        return [
            f"man wearing {primary_hat} candid phone photo",
            "man wearing hat casual outdoor lifestyle",
            f"man wearing {primary_hat} selfie natural light",
        ]
    if persona == "woman":
        return [
            f"woman wearing {primary_hat} candid phone photo",
            "woman wearing hat casual lifestyle natural light",
            f"woman wearing {primary_hat} selfie smartphone photo",
        ]
    if persona == "couple":
        return [
            f"couple wearing {primary_hat} candid outdoor phone photo",
            "couple wearing caps casual lifestyle natural light",
            f"couple wearing {primary_hat} travel candid smartphone photo",
        ]
    if persona == "family":
        return [
            f"family wearing {primary_hat} candid outdoor natural light",
            "family wearing caps casual phone photo",
            f"family wearing {primary_hat} travel lifestyle candid",
        ]
    return [
        f"friends wearing {primary_hat} candid smartphone photo",
        "friends wearing caps outdoor casual lifestyle",
        f"friends wearing {primary_hat} park candid phone photo",
    ]


def _keyword_feedback_scores(
    outcomes: Iterable[tuple[str, str] | tuple[str, str, str | None]],
) -> dict[str, float]:
    stats: dict[str, list[int]] = {}
    useful_statuses = {"approved", "import_queued", "importing", "drive_ready"}
    for outcome in outcomes:
        query, status = outcome[0].casefold(), outcome[1]
        reference_label = outcome[2] if len(outcome) > 2 else None
        row = stats.setdefault(query, [0, 0, 0, 0])
        row[0] += 1
        if status in useful_statuses:
            row[1] += 1
        if reference_label == "good":
            row[2] += 1
        elif reference_label == "bad":
            row[3] += 1

    scores: dict[str, float] = {}
    for query, (evaluated, approved, ref_good, ref_bad) in stats.items():
        reference_reviews = ref_good + ref_bad
        reference_signal = (
            (ref_good - ref_bad) / reference_reviews
            if reference_reviews
            else 0.0
        )
        approved_signal = approved / evaluated if evaluated else 0.0
        scores[query] = 1.25 * reference_signal + 0.35 * approved_signal
    return scores


def build_campaign_search_queries(
    *,
    name: str = "",
    queries: Iterable[str] = (),
    product_snapshot: dict[str, Any] | None = None,
    reject_headwear: bool = False,
    outcomes: Iterable[tuple[str, str] | tuple[str, str, str | None]] = (),
    max_queries: int = MAX_CAMPAIGN_SEARCH_QUERIES,
) -> list[str]:
    original = [str(query or "").strip() for query in queries if str(query or "").strip()]
    if reject_headwear:
        return list(dict.fromkeys(original))[:max_queries]
    if detect_campaign_keyword_intent(
        name=name,
        queries=original,
        product_snapshot=product_snapshot,
    ) != "hat_people":
        return list(dict.fromkeys(original))[:max_queries]

    text = _combined_text(
        name=name,
        queries=original,
        product_snapshot=product_snapshot,
    )
    personas = _detected_personas(text)
    explicit_hat_type = _hat_type(text)

    template_rows = {
        persona: _persona_templates(persona, explicit_hat_type)
        for persona in personas
    }
    generated: list[str] = []
    max_templates = max((len(rows) for rows in template_rows.values()), default=0)
    for index in range(max_templates):
        for persona in personas:
            rows = template_rows[persona]
            if index < len(rows):
                generated.append(rows[index])

    preserved = [
        query for query in original
        if _existing_query_is_worth_preserving(query)
    ][:2]

    candidates: list[str] = []
    seen: set[str] = set()
    for query in [*preserved, *generated]:
        clean = query.strip()
        key = clean.casefold()
        if not clean or key in seen or query_is_suppressed_for_reference_search(clean):
            continue
        seen.add(key)
        candidates.append(clean)

    feedback_scores = _keyword_feedback_scores(outcomes)
    preserved_keys = {query.casefold() for query in preserved}
    original_order = {query.casefold(): index for index, query in enumerate(candidates)}

    def candidate_score(query: str) -> tuple[float, float]:
        key = query.casefold()
        learned = feedback_scores.get(key)
        if learned is None:
            learned = 0.12
        manual_bonus = 0.08 if key in preserved_keys else 0.0
        return learned + manual_bonus, -float(original_order[key])

    candidates.sort(key=candidate_score, reverse=True)
    return candidates[:max_queries] or list(dict.fromkeys(original))[:max_queries]
