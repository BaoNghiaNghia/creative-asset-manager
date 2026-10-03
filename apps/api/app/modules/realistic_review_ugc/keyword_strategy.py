from __future__ import annotations

import re
from typing import Any, Iterable

from app.modules.realistic_review_ugc.product_context import product_context_search_queries


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
        for key in (
            "name",
            "product_type",
            "brand",
            "source_category",
            "source_description",
            "color",
            "material",
            "fit_notes",
            "brim_style",
            "crown_profile",
        ):
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


def campaign_learning_intent(
    *,
    campaign_id: str,
    name: str = "",
    queries: Iterable[str] = (),
    product_snapshot: dict[str, Any] | None = None,
) -> str:
    detected = detect_campaign_keyword_intent(
        name=name,
        queries=queries,
        product_snapshot=product_snapshot,
    )
    if detected:
        return detected

    if isinstance(product_snapshot, dict):
        product_hint = (
            product_snapshot.get("product_type")
            or product_snapshot.get("name")
            or product_snapshot.get("sku")
        )
        if product_hint:
            tokens = re.findall(r"\w+", str(product_hint).casefold(), flags=re.UNICODE)
            if tokens:
                return "product:" + "-".join(tokens[:4])

    return "campaign:" + str(campaign_id)


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
    outcomes: Iterable[
        tuple[str, str]
        | tuple[str, str, str | None]
        | tuple[str, str, str | None, str | None]
    ],
) -> dict[str, float]:
    stats: dict[str, list[int]] = {}
    useful_statuses = {"approved", "import_queued", "importing", "drive_ready"}
    for outcome in outcomes:
        query, status = outcome[0].casefold(), outcome[1]
        reference_label = outcome[2] if len(outcome) > 2 else None
        context_label = outcome[3] if len(outcome) > 3 else None
        row = stats.setdefault(query, [0, 0, 0, 0, 0, 0])
        row[0] += 1
        if status in useful_statuses:
            row[1] += 1
        if reference_label == "good":
            row[2] += 1
        elif reference_label == "bad":
            row[3] += 1
        if context_label == "good":
            row[4] += 1
        elif context_label == "wrong":
            row[5] += 1

    scores: dict[str, float] = {}
    for query, (evaluated, approved, ref_good, ref_bad, context_good, context_wrong) in stats.items():
        reference_reviews = ref_good + ref_bad
        reference_signal = (
            (ref_good - ref_bad) / reference_reviews
            if reference_reviews
            else 0.0
        )
        context_reviews = context_good + context_wrong
        context_signal = (
            (context_good - context_wrong) / context_reviews
            if context_reviews
            else 0.0
        )
        approved_signal = approved / evaluated if evaluated else 0.0
        scores[query] = (
            1.25 * reference_signal
            + 0.90 * context_signal
            + 0.35 * approved_signal
        )
    return scores


def build_campaign_search_queries(
    *,
    name: str = "",
    queries: Iterable[str] = (),
    protected_queries: Iterable[str] = (),
    product_snapshot: dict[str, Any] | None = None,
    discovery_mode: str = "keyword",
    product_context: dict[str, Any] | None = None,
    reject_headwear: bool = False,
    outcomes: Iterable[
        tuple[str, str]
        | tuple[str, str, str | None]
        | tuple[str, str, str | None, str | None]
    ] = (),
    max_queries: int = MAX_CAMPAIGN_SEARCH_QUERIES,
) -> list[str]:
    original = [str(query or "").strip() for query in queries if str(query or "").strip()]
    protected = list(dict.fromkeys(
        str(query or "").strip()
        for query in protected_queries
        if str(query or "").strip()
    ))

    if discovery_mode == "product_context":
        # Product Context treats manual queries as anchors, not the whole pool.
        # Reserve most slots for server-derived semantic scene searches.
        protected = protected[:2]
        feedback_scores = _keyword_feedback_scores(outcomes)
        level_weight = {"direct": 0.30, "text_match": 0.27, "adjacent": 0.18, "generic": 0.08}
        contextual = product_context_search_queries(product_context)
        protected_keys = {query.casefold() for query in protected}
        selected: list[str] = []
        selected_keys: set[str] = set()

        for query in protected:
            key = query.casefold()
            if key in selected_keys:
                continue
            selected.append(query)
            selected_keys.add(key)
            if len(selected) >= max_queries:
                return selected[:max_queries]

        ranked_context = sorted(
            contextual,
            key=lambda item: (
                feedback_scores.get(item[0].casefold(), 0.12)
                + level_weight.get(item[1], 0.0)
            ),
            reverse=True,
        )
        for query, _level in ranked_context:
            key = query.casefold()
            if key in selected_keys or query_is_suppressed_for_reference_search(query):
                continue
            selected.append(query)
            selected_keys.add(key)
            if len(selected) >= max_queries:
                break

        if len(selected) < max_queries:
            for query in original:
                key = query.casefold()
                if key in selected_keys:
                    continue
                selected.append(query)
                selected_keys.add(key)
                if len(selected) >= max_queries:
                    break
        return selected or list(dict.fromkeys(original))[:max_queries]

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

    protected_keys = {query.casefold() for query in protected}
    preserved = [
        query for query in original
        if query.casefold() not in protected_keys
        and _existing_query_is_worth_preserving(query)
    ][:2]

    candidates: list[str] = []
    seen: set[str] = set()
    for query in [*protected, *preserved, *generated]:
        clean = query.strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        if (
            key not in protected_keys
            and query_is_suppressed_for_reference_search(clean)
        ):
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
        manual_adjustment = (
            -0.02
            if key in protected_keys
            else 0.08
            if key in preserved_keys
            else 0.0
        )
        return learned + manual_adjustment, -float(original_order[key])

    candidates.sort(key=candidate_score, reverse=True)
    selected = candidates[:max_queries]
    selected_keys = {query.casefold() for query in selected}
    for protected_query in protected:
        key = protected_query.casefold()
        if key in selected_keys:
            continue
        replacement_index = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if selected[index].casefold() not in protected_keys
            ),
            None,
        )
        if replacement_index is None:
            if len(selected) < max_queries:
                selected.append(protected_query)
                selected_keys.add(key)
            continue
        selected_keys.discard(selected[replacement_index].casefold())
        selected[replacement_index] = protected_query
        selected_keys.add(key)

    remaining = [
        query for query in candidates
        if query.casefold() not in selected_keys
        and query.casefold() not in protected_keys
    ]
    if selected and remaining:
        negatively_learned = [
            query for query in remaining
            if feedback_scores.get(query.casefold(), 0.0) < 0.0
        ]
        unseen = [
            query for query in remaining
            if query.casefold() not in feedback_scores
        ]
        reserve = (
            min(
                negatively_learned,
                key=lambda query: feedback_scores[query.casefold()],
            )
            if negatively_learned
            else unseen[0]
            if unseen
            else remaining[-1]
        )
        replacement_index = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if selected[index].casefold() not in protected_keys
            ),
            None,
        )
        if replacement_index is not None:
            selected[replacement_index] = reserve

    return selected or list(dict.fromkeys(original))[:max_queries]
