"""Tenant-scoped, persistent, low-cost Later matching across Etsy Spy syncs.

Manual Later selects a source design-topic seed. The related queue is an indexed
projection, never a rewrite of Windows Scout records. Dismissals survive sync.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .later_matching import analyze_title, match_title
from .model import EtsySpyLaterMatch as LaterMatch
from .model import EtsySpyLaterSeed as LaterSeed
from .model import EtsySpyListing as Listing, utcnow


def seed_analysis(title: str) -> dict:
    value = analyze_title(title)
    return {"version": 1, "themes": sorted(value.themes),
            "terms": sorted(value.terms)[:80]}


def refresh_later_matches(
    db: Session, tenant: str, listing_keys: Iterable[str] | None = None,
) -> dict[str, int]:
    """Recompute only changed IDs during sync; all active IDs on seed edits.

    A listing with multiple sessions/keywords or Search+Shop is matched once
    by Listing ID, choosing the best corresponding title and seed.
    """
    keys = set(listing_keys) if listing_keys is not None else None
    if keys is not None and not keys:
        return {"matched": 0, "checked": 0}
    seeds = list(db.scalars(select(LaterSeed).where(
        LaterSeed.tenant_id == tenant).order_by(LaterSeed.listing_key)).all())
    # Without seeds, incoming scout batches cannot create new Later rows.
    if not seeds and keys is not None:
        return {"matched": 0, "checked": 0}

    title_query = select(Listing.listing_key, Listing.title).where(
        Listing.tenant_id == tenant, Listing.is_current.is_(True),
    )
    match_query = select(LaterMatch).where(LaterMatch.tenant_id == tenant)
    if keys is not None:
        title_query = title_query.where(Listing.listing_key.in_(keys))
        match_query = match_query.where(LaterMatch.listing_key.in_(keys))
    titles: dict[str, set[str]] = defaultdict(set)
    for listing_key, title in db.execute(title_query):
        if isinstance(title, str) and title:
            titles[listing_key].add(title)
    prior = {item.listing_key: item for item in db.scalars(match_query)}
    seed_by_id = {item.listing_key: item for item in seeds}
    considered = (set(titles) | set(prior)) if keys is None else keys
    changed = 0
    for listing_key in considered:
        old = prior.get(listing_key)
        # Explicit dismissals are user-owned and must never be undone by sync.
        if old is not None and old.state == "dismissed" and listing_key not in seed_by_id:
            continue
        best_score = 0
        best_seed = None
        best_reason = None
        if listing_key in seed_by_id and listing_key in titles:
            best_score, best_seed, best_reason = 100, listing_key, "Được chọn Later"
        elif listing_key in titles:
            for seed in seeds:
                if seed.listing_key == listing_key:
                    continue
                for title in titles[listing_key]:
                    match = match_title(seed.title, title)
                    if match is not None and match.score > best_score:
                        best_score = match.score
                        best_seed = seed.listing_key
                        best_reason = match.reason
        if best_seed is None:
            if old is not None and old.state == "matched":
                db.delete(old)
                changed += 1
            continue
        if old is None:
            db.add(LaterMatch(
                tenant_id=tenant, listing_key=listing_key,
                seed_listing_key=best_seed, score=best_score,
                reason=best_reason, state="matched", updated_at=utcnow(),
            ))
            changed += 1
        elif (old.state != "matched" or old.score != best_score or
              old.seed_listing_key != best_seed or old.reason != best_reason):
            old.seed_listing_key = best_seed
            old.score = best_score
            old.reason = best_reason
            old.state = "matched"
            old.updated_at = utcnow()
            changed += 1
    return {"matched": changed, "checked": len(considered)}
