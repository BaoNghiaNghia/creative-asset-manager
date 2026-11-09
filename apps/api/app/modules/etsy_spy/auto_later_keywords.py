"""Explicit tenant-scoped, deterministic Later routing for seasonal Etsy listings.

These are operator-directed review rules, not blacklist keywords or Scout seeds.
They intentionally do not mutate original Picked rows, source observations,
search histories, or extraction records.
"""
from __future__ import annotations

from typing import Iterable

# Requested for CAM's sole operating tenant on 2026-11-09.
TENANT_AUTO_LATER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "boostgame": ("Thanksgiving", "Halloween"),
}


def keywords_for_tenant(tenant: str) -> tuple[str, ...]:
    return TENANT_AUTO_LATER_KEYWORDS.get(tenant, ())


def match_auto_later(
    tenant: str, title: str | None = None, keyword: str | None = None,
) -> str | None:
    """Classify an exact named season in either Etsy listing title or search keyword."""
    words = keywords_for_tenant(tenant)
    if not words:
        return None
    haystacks = (str(title or "").casefold(), str(keyword or "").casefold())
    for word in words:
        lowered = word.casefold()
        # Long, distinctive seasonal terms; intentionally cover attached Etsy
        # forms such as Halloween-themed as well as whitespace-separated terms.
        if any(lowered in value for value in haystacks):
            return word
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
