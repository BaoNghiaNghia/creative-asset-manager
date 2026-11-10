"""Safely reconcile only legacy cat/dog auto-Later matches after rule tightening.

Dry-run by default. Apply requires an exact candidate fingerprint obtained from
a dry-run against the same database. This never rewrites Scout evidence, Picked,
manual Later seeds, dismissed matches, or unrelated Later topic matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.etsy_spy.auto_later_keywords import match_auto_later
from app.modules.etsy_spy.later_matching import match_title
from app.modules.etsy_spy.later_queue import refresh_later_matches
from app.modules.etsy_spy.model import (
    EtsySpyLaterMatch as LaterMatch,
    EtsySpyLaterSeed as LaterSeed,
    EtsySpyListing as Listing,
    EtsySpyPicked as Picked,
)

LEGACY_REASONS = ("Cùng chủ đề: cat", "Cùng chủ đề: dog")


def candidate_fingerprint(rows):
    raw = "\n".join(
        f"{row.listing_key}|{row.seed_listing_key}|{row.score}|{row.reason}"
        for row in sorted(rows, key=lambda item: item.listing_key)
    )
    return hashlib.sha256(raw.encode("utf8")).hexdigest()[:20]


def report(db, tenant: str, rows):
    keys = {row.listing_key for row in rows}
    seeds = list(db.scalars(select(LaterSeed).where(
        LaterSeed.tenant_id == tenant).order_by(LaterSeed.listing_key)).all())
    titles = defaultdict(set)
    for key, title, keyword in db.execute(select(
        Listing.listing_key, Listing.title, Listing.keyword).where(
        Listing.tenant_id == tenant, Listing.is_current.is_(True),
    )):
        titles[key].add((title, keyword))
    picked = set(db.scalars(select(Picked.listing_key).where(
        Picked.tenant_id == tenant, Picked.listing_key.in_(keys))).all())
    manual = {seed.listing_key for seed in seeds}
    would_unmatch = []
    still_auto = []
    retained = []
    for row in rows:
        matches = titles.get(row.listing_key, set())
        any_specific_match = row.listing_key in manual or any(
            match_title(seed.title, title) is not None
            for seed in seeds if seed.listing_key != row.listing_key
            for title, _ in matches
        )
        if any_specific_match:
            retained.append(row.listing_key)
            continue
        would_unmatch.append(row.listing_key)
        if any(match_auto_later(tenant, title, keyword) for title, keyword in matches):
            still_auto.append(row.listing_key)
    newly_available = set(would_unmatch) - set(still_auto) - picked
    return {
        "tenant": tenant,
        "candidate_count": len(rows),
        "fingerprint": candidate_fingerprint(rows),
        "manual_seeds_retained": len(manual.intersection(keys)),
        "would_remove_broad_auto_matches": len(would_unmatch),
        "would_remain_related_later": len(retained),
        "would_remain_seasonal_later": len(still_auto),
        "would_already_be_picked": len(picked.intersection(would_unmatch)),
        "potentially_available_after_reconcile": len(newly_available),
        "potentially_available_listing_ids": sorted(newly_available)[:30],
        "reviewed_candidate_ids": sorted(keys)[:15],
    }


def main():
    parser = argparse.ArgumentParser()
    # Explicit tenant identity avoids a misleading zero-row dry run when the
    # display slug differs from the production tenant database key.
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-fingerprint", default="")
    parser.add_argument("--max-candidates", type=int, default=100)
    args = parser.parse_args()
    if args.apply and (len(args.expected_fingerprint) != 20):
        parser.error("--apply requires --expected-fingerprint from a dry run")
    with SessionLocal() as db:
        query = select(LaterMatch).where(
            LaterMatch.tenant_id == args.tenant,
            LaterMatch.state == "matched",
            LaterMatch.reason.in_(LEGACY_REASONS),
        ).order_by(LaterMatch.listing_key)
        if args.apply:
            query = query.with_for_update()
        rows = list(db.scalars(query).all())
        if len(rows) > args.max_candidates:
            raise SystemExit("Refusing excessive candidate count; audit the latest data first")
        result = report(db, args.tenant, rows)
        if args.apply:
            if result["fingerprint"] != args.expected_fingerprint:
                raise SystemExit("Candidate fingerprint changed; repeat the dry run")
            ids = [row.listing_key for row in rows]
            change = refresh_later_matches(db, args.tenant, listing_keys=ids)
            db.flush()
            after = list(db.scalars(select(LaterMatch).where(
                LaterMatch.tenant_id == args.tenant,
                LaterMatch.listing_key.in_(ids),
                LaterMatch.state == "matched",
            )).all()) if ids else []
            result["actual_matched_after"] = len(after)
            result["recomputed"] = change
            db.commit()
            result["applied"] = True
        else:
            db.rollback()
            result["applied"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
