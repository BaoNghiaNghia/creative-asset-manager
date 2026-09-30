from __future__ import annotations

from collections.abc import Iterable
from typing import Any


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


def derive_product_context_profile(
    *,
    product_snapshot: dict[str, Any] | None,
    campaign_name: str = "",
    queries: Iterable[str] = (),
    config: dict[str, Any] | None = None,
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

    text = _product_context_text(
        product_snapshot,
        campaign_name=campaign_name,
        queries=queries,
        notes=notes,
    )
    detected_themes = _detect_themes(text) if auto_context else []
    themes = _clean_list([*operator_themes, *detected_themes], limit=8)

    direct: list[str] = []
    adjacent: list[str] = []
    for theme in themes:
        templates = _CONTEXT_QUERY_TEMPLATES.get(theme, {})
        direct.extend(templates.get("direct", ()))
        adjacent.extend(templates.get("adjacent", ()))

    for scene in preferred_scenes:
        direct.append(f"{scene} candid phone photo")

    search_clusters = {
        "direct": _clean_list(direct, limit=16),
        "adjacent": _clean_list(adjacent, limit=12),
        "generic": list(_GENERIC_CONTEXT_QUERIES),
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
        "search_clusters": search_clusters,
        "source": {
            "product_name": product_name,
            "product_type": product_type,
            "has_product_snapshot": isinstance(product_snapshot, dict),
        },
        "version": "product-context-v1",
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
