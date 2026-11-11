"""Audit/recompute derived Etsy Later relationships after occasion-scope change.

Read-only by default. Never changes automatic Later (computed in SQL), source
records, Picked history, manual Later seeds, or dismissed relationships.
Apply requires a fresh review fingerprint AND a verified production backup
path. Run through the approved deployment/maintenance process, not on a
developer machine pointed accidentally at production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.etsy_spy.auto_later_keywords import batch_auto_later_labels
from app.modules.etsy_spy.later_matching import match_title
from app.modules.etsy_spy.later_queue import refresh_later_matches
from app.modules.etsy_spy.model import (
    EtsySpyLaterMatch as LaterMatch,
    EtsySpyLaterSeed as LaterSeed,
    EtsySpyListing as Listing,
    EtsySpyPicked as Picked,
)


def audit(db, tenant: str):
    matches = list(db.scalars(select(LaterMatch).where(
        LaterMatch.tenant_id == tenant, LaterMatch.state == "matched",
    ).order_by(LaterMatch.listing_key)).all())
    seeds = list(db.scalars(select(LaterSeed).where(
        LaterSeed.tenant_id == tenant).order_by(LaterSeed.listing_key)).all())
    seed_ids = {seed.listing_key for seed in seeds}
    ids = {m.listing_key for m in matches}
    titles = defaultdict(set)
    records = []
    if ids:
        for key, title, keyword in db.execute(select(
            Listing.listing_key, Listing.title, Listing.keyword,
        ).where(Listing.tenant_id == tenant,
                Listing.is_current.is_(True),
                Listing.listing_key.in_(ids))):
            titles[key].add(title or "")
            records.append((key, title, keyword))
    auto = batch_auto_later_labels(tenant, records)
    picked = set(db.scalars(select(Picked.listing_key).where(
        Picked.tenant_id == tenant, Picked.listing_key.in_(ids),
    )).all()) if ids else set()
    candidates = []
    for row in matches:
        # Explicit manually selected Later products are always retained.
        if row.listing_key in seed_ids or not titles.get(row.listing_key):
            continue
        best_score = 0
        best_id = None
        best_reason = None
        for seed in seeds:
            if seed.listing_key == row.listing_key:
                continue
            for title in titles[row.listing_key]:
                found = match_title(seed.title, title)
                if found and found.score > best_score:
                    best_score, best_id, best_reason = (
                        found.score, seed.listing_key, found.reason)
        if (row.seed_listing_key, row.score, row.reason) != (
                best_id, best_score, best_reason):
            candidates.append(row)
    lines = [
        "|".join((row.listing_key, str(row.seed_listing_key or ""),
                  str(row.score), str(row.reason), str(row.updated_at)))
        for row in candidates
    ]
    fingerprint = hashlib.sha256("\n".join(lines).encode("utf8")).hexdigest()[:24]
    return {
        "tenant": tenant,
        "derived_matched_examined": len(matches),
        "manual_seed_count": len(seeds),
        "would_recompute": len(candidates),
        "would_not_be_auto_later": sum(m.listing_key not in auto for m in candidates),
        "would_become_eligible_upper_bound": sum(
            m.listing_key not in auto and m.listing_key not in picked
            for m in candidates),
        "fingerprint": fingerprint,
        "listing_ids": sorted(m.listing_key for m in candidates),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-fingerprint", default="")
    parser.add_argument("--backup-path", default="")
    parser.add_argument("--max-candidates", type=int, default=2000)
    args = parser.parse_args()
    if args.apply:
        if len(args.expected_fingerprint) != 24:
            parser.error("apply requires a fresh 24-character dry-run fingerprint")
        backup = Path(args.backup_path)
        if not backup.is_file() or (datetime.now(timezone.utc).timestamp() -
                                     backup.stat().st_mtime) > 6 * 3600:
            parser.error("apply requires an existing verified backup from the last 6 hours")
    with SessionLocal() as db:
        if args.apply:
            # Keep the reviewed candidate set stable during reconciliation.
            db.execute(select(LaterMatch).where(
                LaterMatch.tenant_id == args.tenant,
                LaterMatch.state == "matched",
            ).with_for_update()).all()
        report = audit(db, args.tenant)
        if report["would_recompute"] > args.max_candidates:
            raise SystemExit("Candidate cap exceeded; inspect before applying")
        if args.apply:
            if report["fingerprint"] != args.expected_fingerprint:
                raise SystemExit("Candidate fingerprint changed; repeat dry-run")
            before = set(report.pop("listing_ids"))
            changed = refresh_later_matches(db, args.tenant, listing_keys=before)
            db.commit()
            report["recomputed"] = changed
            report["applied"] = True
        else:
            report["listing_ids"] = report["listing_ids"][:40]
            report["applied"] = False
            db.rollback()
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
