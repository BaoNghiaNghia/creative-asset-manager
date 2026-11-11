"""Conservative tenant-scoped automatic Later routing by product occasion.

Only explicit occasion evidence in the LISTING TITLE may trigger auto-Later.
Search terms, browse keywords, broad category badges and sibling listings
are NOT evidence of the design's occasion. Conflicting titles for the same
Etsy listing ID stay reviewable rather than being silently auto-routed.

These helpers are shared by the API's SQL predicate and response labels.
The rules are derived/read-only: source, Picked and manual Later stay intact.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from sqlalchemy import func, literal

TENANT_AUTO_LATER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "boostgame": ("Thanksgiving", "Halloween"),
    "creative-assets-prod": ("Thanksgiving", "Halloween"),
}

# Very high-signal occasion phrases only. Broad design elements (pumpkin,
# ghost, witch, spooky, haunted, harvest) are deliberately NOT auto-Later.
# "hocus pocus" is a movie/quotation too, not proof of Halloween by itself.
EXPLICIT_OCCASIONS: dict[str, tuple[str, ...]] = {
    "thanksgiving": ("thanksgiving", "friendsgiving", "turkey day",
                     "gobble gobble"),
    "halloween": ("halloween", "trick or treat", "all hallows eve"),
}

# A clearly different occasion in any observed title vetoes auto-Later for
# the whole listing ID. No prioritizing a random Search/Shop alias.
OTHER_OCCASION_GROUPS: dict[str, tuple[str, ...]] = {
    "christmas": ("christmas", "xmas", "santa", "rudolph", "reindeer",
                  "grinch", "merry christmas", "elf on the shelf"),
    "easter": ("easter",),
    "valentines": ("valentines", "valentine"),
    "st_patricks": ("st patricks", "st patrick"),
    "independence_day": ("4th of july", "fourth of july"),
    "mothers_day": ("mothers day",),
    "fathers_day": ("fathers day",),
    "new_year": ("new year", "new years"),
    "birthday": ("birthday", "happy birthday"),
    "graduation": ("graduation", "graduate day"),
    "wedding": ("wedding", "bridal shower"),
    "anniversary": ("anniversary",),
}
OTHER_OCCASIONS = tuple(p for phrases in OTHER_OCCASION_GROUPS.values()
                        for p in phrases)


def keywords_for_tenant(tenant: str) -> tuple[str, ...]:
    return TENANT_AUTO_LATER_KEYWORDS.get(tenant, ())


def _pattern(phrases: tuple[str, ...]) -> str:
    # Identical normalized word/phrase boundaries in SQL and Python.
    values = "|".join(re.escape(p).replace(r"\ ", "[^a-z0-9]+") for p in phrases)
    return r"(^|[^a-z0-9])(?:" + values + r")([^a-z0-9]|$)"


COMPILED = {code: re.compile(_pattern(phrases))
            for code, phrases in EXPLICIT_OCCASIONS.items()}
OTHER_PATTERN = re.compile(_pattern(OTHER_OCCASIONS))
OTHER_COMPILED = {code: re.compile(_pattern(phrases))
                  for code, phrases in OTHER_OCCASION_GROUPS.items()}


def explicit_title_occasions(title: str | None) -> frozenset[str]:
    value = str(title or "").lower()
    return frozenset(code for code, pattern in COMPILED.items()
                     if pattern.search(value))


def other_occasion_in_title(title: str | None) -> bool:
    return bool(OTHER_PATTERN.search(str(title or "").lower()))


def title_occasion_scope(title: str | None) -> frozenset[str]:
    """Named explicit occasions; broad motif words never assign a season."""
    value = str(title or "").lower()
    return explicit_title_occasions(title) | frozenset(
        code for code, pattern in OTHER_COMPILED.items() if pattern.search(value)
    )


def title_occasion_sql(listing, occasion: str):
    """SQL counterpart of explicit_title_occasions, title only."""
    phrases = EXPLICIT_OCCASIONS.get(occasion)
    if not phrases:
        return literal(False)
    return func.lower(func.coalesce(listing.title, "")).regexp_match(
        _pattern(phrases))


def other_occasion_sql(listing):
    return func.lower(func.coalesce(listing.title, "")).regexp_match(
        _pattern(OTHER_OCCASIONS))


def match_auto_later(
    tenant: str, title: str | None = None, keyword: str | None = None,
) -> str | None:
    """One title's explicit occasion, never its source search keyword."""
    del keyword  # Source keyword intentionally has NO routing authority.
    configured = {name.lower(): name for name in keywords_for_tenant(tenant)}
    if not configured or other_occasion_in_title(title):
        return None
    matched = explicit_title_occasions(title)
    if len(matched) != 1:
        return None
    return configured.get(next(iter(matched)))


def batch_auto_later_labels(
    tenant: str, records: Iterable[tuple[str, str | None, str | None]],
) -> dict[str, str]:
    """Aggregate ALL current title aliases per listing ID before deciding.

    A seasonal title plus a distinct generic alias is fine; any explicit
    competing occasion in another alias vetoes the entire listing ID.
    """
    configured = {name.lower(): name for name in keywords_for_tenant(tenant)}
    if not configured:
        return {}
    occasions: dict[str, set[str]] = defaultdict(set)
    conflicting: set[str] = set()
    for listing_key, title, _keyword in records:
        key = str(listing_key)
        detected = explicit_title_occasions(title)
        occasions[key].update(detected)
        if other_occasion_in_title(title):
            conflicting.add(key)
    return {
        key: configured[next(iter(found))]
        for key, found in occasions.items()
        if key not in conflicting and len(found) == 1
        and next(iter(found)) in configured
    }
