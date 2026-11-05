"""Source-artwork-driven seasonal discovery, independent of calendar date.

Only explicit artwork/name evidence selects a specific holiday. A broad
"holiday" theme, winter clothing or family gathering is NOT proof of Christmas.
This module is deterministic and adds no AI or external API cost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSeason:
    key: str
    label: str
    scene: str
    decoration: str


_SEASONS: tuple[tuple[SourceSeason, tuple[str, ...]], ...] = (
    (SourceSeason(
        "christmas", "Christmas", "cozy Christmas living room",
        "decorated Christmas tree and twinkle lights",
    ), (
        r"\bchristmas\b", r"\bxmas\b", r"\bx-mas\b", r"\bnoel\b",
        r"\bmerry\s+christmas\b", r"\bsanta(?:\s+claus)?\b",
        r"\bchristmas\s+(?:tree|stocking|ornament)\b",
        r"\breindeer\b", r"\bgingerbread\b", r"\bcandy\s+cane\b",
        r"\bmistletoe\b", r"\belf\s+on\s+the\s+shelf\b",
    )),
    (SourceSeason(
        "halloween", "Halloween", "Halloween decorated home",
        "jack o lanterns and spooky porch decor",
    ), (
        r"\bhalloween\b", r"\btrick\s+or\s+treat\b",
        r"\bjack\s+o\s+lantern\b", r"\bspooky\s+season\b",
        r"\bwitch(?:es)?\s+hat\b",
    )),
    (SourceSeason(
        "thanksgiving", "Thanksgiving", "Thanksgiving family dinner",
        "autumn table settings and fall harvest decorations",
    ), (
        r"\bthanksgiving\b", r"\bturkey\s+day\b",
        r"\bthankful\s+and\s+blessed\b",
    )),
    (SourceSeason(
        "valentines", "Valentine's Day", "Valentine's Day celebration",
        "Valentine heart decorations and flowers",
    ), (
        r"\bvalentine(?:'?s)?(?:\s+day)?\b",
        r"\bgalentine(?:'?s)?(?:\s+day)?\b",
        r"\bbe\s+mine\b",
    )),
    (SourceSeason(
        "easter", "Easter", "Easter family brunch",
        "Easter eggs and spring flower decorations",
    ), (
        r"\beaster\b", r"\beaster\s+bunny\b", r"\begg\s+hunt\b",
    )),
    (SourceSeason(
        "fourth_of_july", "Fourth of July", "July Fourth backyard celebration",
        "American flags and red white blue party decor",
    ), (
        r"\b(?:4th|fourth)\s+of\s+july\b", r"\b(?:july\s+4|july\s+fourth)\b",
        r"\bindependence\s+day\b",
    )),
    (SourceSeason(
        "new_year", "New Year's Eve", "New Year's Eve party",
        "festive New Year party lights and confetti",
    ), (
        r"\bnew\s+year(?:'?s)?(?:\s+eve|\s+day)?\b",
        r"\bhappy\s+new\s+year\b", r"\bnye\s+party\b",
    )),
    (SourceSeason(
        "st_patricks", "St Patrick's Day", "St Patrick's Day gathering",
        "green shamrocks and festive decorations",
    ), (
        r"\bst\.?\s+patrick(?:'?s)?(?:\s+day)?\b",
        r"\bsaint\s+patrick(?:'?s)?\b",
    )),
    (SourceSeason(
        "winter", "winter", "cozy winter cabin",
        "snowy outdoor scenery and winter decorations",
    ), (r"\bwinter\b", r"\bwintertime\b", r"\bsnow\s+day\b")),
    (SourceSeason(
        "autumn", "autumn", "cozy autumn living room",
        "fall leaves and harvest decorations",
    ), (r"\bautumn\b", r"\bfall\s+season\b", r"\bfall\s+leaves\b")),
    (SourceSeason(
        "summer", "summer", "summer backyard gathering",
        "sunny park and outdoor picnic scenery",
    ), (r"\bsummer\b", r"\bsummertime\b")),
    (SourceSeason(
        "spring", "spring", "spring garden gathering",
        "spring flowers and blooming garden",
    ), (r"\bspring\b", r"\bspringtime\b")),
)


def _season_in_text(text: str) -> SourceSeason | None:
    matches = [
        season for season, expressions in _SEASONS
        if any(re.search(expression, text, re.IGNORECASE) for expression in expressions)
    ]
    # If two incompatible holidays appear in the same field, avoid arbitrarily
    # forcing the wrong seasonal context. A dedicated product title wins below.
    specific = [season for season in matches if season.key not in {
        "winter", "autumn", "summer", "spring"
    }]
    matching = specific or matches
    return matching[0] if len(matching) == 1 else None


def detect_source_season(
    product_snapshot: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
) -> SourceSeason | None:
    """Find season in filename or artwork evidence, never today's calendar.

    Read artwork text/identity/cues before generated scene suggestions.
    An unqualified theme such as 'holiday' or generic 'snowflake' is not
    enough to select Christmas.
    """
    product = dict(product_snapshot or {})
    context = dict(profile or {})
    visual = context.get("visual_context")
    if not isinstance(visual, dict):
        visual = {}
    fields: list[str] = [
        str(product.get("name") or ""),
        str(product.get("sku") or ""),
        str(product.get("source_description") or ""),
        " ".join(str(x) for x in (visual.get("embroidery_text") or [])[:6]),
        str(visual.get("embroidery_identity") or ""),
        " ".join(str(x) for x in (visual.get("product_cues") or [])[:10]),
        " ".join(str(x) for x in (visual.get("occasion_hints") or [])[:6]),
        " ".join(str(x) for x in (visual.get("themes") or [])[:8]),
        " ".join(str(x) for x in (visual.get("scene_hints") or [])[:6]),
        str(visual.get("summary") or ""),
    ]
    for value in fields:
        # Hyphenation in source filenames and AI tags is not a word boundary
        # for patterns containing multiple words.
        normalized = re.sub(r"[_-]+", " ", value)
        matched = _season_in_text(normalized)
        if matched is not None:
            return matched
    return None



def seasonal_hat_claim_search(
    product_snapshot: dict[str, Any] | None,
    product_context: dict[str, Any] | None,
    existing_queries: list[str],
) -> tuple[list[str], dict[str, Any]] | None:
    """Add season-specific, cap-visible discovery targets to a GatorHats claim.

    Preserves all legacy search clusters and never modifies persisted campaign
    settings. Explicit seasonal source evidence is required.
    """
    season = detect_source_season(product_snapshot, product_context)
    if season is None:
        return None
    selfie = (
        f"{season.label} {season.scene} person wearing embroidered cap "
        "front view casual selfie photo"
    )
    held = (
        f"{season.label} {season.decoration} hand holding embroidered cap "
        "front view natural photo"
    )
    lifestyle = (
        f"{season.label} {season.scene} casual person in baseball cap "
        "candid phone photo"
    )
    context = dict(product_context or {})
    clusters = (
        dict(context.get("search_clusters"))
        if isinstance(context.get("search_clusters"), dict)
        else {}
    )
    for key, extra, limit in (
        ("selfie_wearing_hat", selfie, 8),
        ("hand_holding_hat", held, 8),
        ("direct", lifestyle, 16),
    ):
        old = clusters.get(key)
        values = [str(value).strip() for value in old] if isinstance(old, list) else []
        clusters[key] = list(dict.fromkeys([extra, *values]))[:limit]
    context["search_clusters"] = clusters
    return list(dict.fromkeys([selfie, held, lifestyle, *existing_queries])), context
