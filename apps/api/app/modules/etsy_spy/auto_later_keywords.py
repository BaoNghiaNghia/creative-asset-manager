"""Operator-owned automatic Later routing using CAM's daily topic groups.

The same category phrase tables classify past and newly synced Etsy listings.
This is view routing, NOT a blacklist or source/Picked database rewrite.
"""
from __future__ import annotations

import re
from typing import Iterable

from .groups import CATEGORY_GROUPS

# A tenant-scoped rule; matching phrases are taken from CATEGORY_GROUPS.
TENANT_AUTO_LATER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "boostgame": ("Thanksgiving", "Halloween"),
}


def keywords_for_tenant(tenant: str) -> tuple[str, ...]:
    return TENANT_AUTO_LATER_KEYWORDS.get(tenant, ())


def _padded_source(title: str | None, keyword: str | None) -> str:
    # Mirror groups._words: case, punctuation and padded word boundaries.
    combined = str(title or "") + " " + str(keyword or "")
    normalized = re.sub(r"[-_./&,'’:!?()#]", " ", combined.lower())
    return " " + " ".join(normalized.split()) + " "


def match_auto_later(
    tenant: str, title: str | None = None, keyword: str | None = None,
) -> str | None:
    """Return the first configured category matching title OR search keyword."""
    configured = {name.lower(): name for name in keywords_for_tenant(tenant)}
    if not configured:
        return None
    source = _padded_source(title, keyword)
    for code, _label, phrases in CATEGORY_GROUPS:
        if code in configured and any(" " + phrase + " " in source for phrase in phrases):
            return configured[code]
    return None


def batch_auto_later_labels(
    tenant: str, records: Iterable[tuple[str, str | None, str | None]],
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for listing_key, title, keyword in records:
        match = match_auto_later(tenant, title, keyword)
        if match:
            labels.setdefault(str(listing_key), match)
    return labels
