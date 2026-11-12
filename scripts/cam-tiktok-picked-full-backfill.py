#!/usr/bin/env python3
"""Idempotently queue v6 TikTok SEO for *all* Picked Etsy products.

Latest completed v6 jobs remain untouched. Completed older versions become
new immutable v6 regeneration jobs. Active jobs are not duplicated, and
missing source images are reported instead of faking completion.
Read-only unless --apply. Backfill is low priority to avoid delaying manual jobs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os

from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.etsy_spy.model import EtsySpyPicked, EtsySpyListing
from app.modules.etsy_spy.tiktok_skill import (
    JOB_TYPE, PROFILE_VERSION, execution_engine, is_enabled, latest_job,
    queue_on_pick, regenerate_completed_draft, retry_failed_draft,
    verified_image_url,
)
from app.modules.processing.model import ProcessingJobModel

BACKFILL_PRIORITY = 5  # interactive Picked requests use priority 14


def pick_latest_listing(db, tenant: str, key: str) -> EtsySpyListing | None:
    """Recover archived Picked photos when a fresh current Scout row is absent."""
    candidates = list(db.scalars(select(EtsySpyListing).where(
        EtsySpyListing.tenant_id == tenant,
        EtsySpyListing.listing_key == key,
    ).order_by(EtsySpyListing.is_current.desc(),
               EtsySpyListing.observed_at.desc(),
               EtsySpyListing.updated_at.desc()).limit(25)).all())
    for listing in candidates:
        metadata = listing.metadata_json if isinstance(listing.metadata_json, dict) else {}
        if verified_image_url(metadata.get("fullImageUrl")) or verified_image_url(
                metadata.get("thumbnailUrl")):
            return listing
    return None


def execute(tenant: str, limit: int, apply: bool) -> dict:
    if not is_enabled() or execution_engine() != "codex":
        raise SystemExit("requires_codex_listing_worker_enabled")
    counts = Counter()
    with SessionLocal() as db:
        rows = list(db.scalars(select(EtsySpyPicked).where(
            EtsySpyPicked.tenant_id == tenant,
        ).order_by(EtsySpyPicked.picked_at.asc(),
                   EtsySpyPicked.listing_key.asc())).all())
        keys = [(row.listing_key, row.picked_at) for row in rows]
    counts["total_picked"] = len(keys)
    counts["selected"] = min(len(keys), limit)
    for key, picked_at in keys[:limit]:
        with SessionLocal() as db:
            listing = pick_latest_listing(db, tenant, key)
            if listing is None:
                counts["missing_verified_image_source"] += 1
                continue
            if not listing.is_current:
                counts["archived_source_recovered"] += 1
            current = latest_job(db, tenant, key)
            payload = current.payload_json if current and isinstance(
                current.payload_json, dict) else {}
            state = current.status if current else "not_queued"
            version = payload.get("skill_version")
            if current and current.cancellation_requested:
                counts["cancelled_skipped"] += 1
                continue
            if current and state in ("pending", "processing", "retry"):
                counts["already_active"] += 1
                continue
            if current and state == "completed" and version == PROFILE_VERSION:
                counts["current_v6_completed"] += 1
                continue
            if not apply:
                if current and state == "completed":
                    counts["will_upgrade_legacy"] += 1
                elif current and state == "failed" and version == PROFILE_VERSION:
                    counts["will_retry_failed"] += 1
                else:
                    counts["will_create_missing"] += 1
                continue
            before_id = current.id if current else None
            try:
                if current and state == "completed":
                    job = regenerate_completed_draft(db, tenant, listing, picked_at)
                elif current and state == "failed" and version == PROFILE_VERSION:
                    job = retry_failed_draft(db, tenant, listing, picked_at)
                else:
                    job = queue_on_pick(db, tenant, listing, picked_at)
                if job is None:
                    counts["no_valid_source_or_quota"] += 1
                    continue
                if job.id == before_id:
                    if job.status in ("pending", "retry") and state == "failed":
                        counts["retries_queued"] += 1
                    else:
                        counts["already_existing"] += 1
                elif current and state == "completed":
                    counts["legacy_regenerated"] += 1
                else:
                    counts["new_queued"] += 1
                if job.id != before_id and job.status == "pending":
                    # The worker is serialized through a Codex home lock;
                    # lower priority ensures manual jobs get first pick.
                    job.priority = BACKFILL_PRIORITY
                db.commit()
            except Exception:
                db.rollback()
                counts["queue_error"] += 1
    return {"mode": "apply" if apply else "dry_run", "tenant": tenant,
            "profile": PROFILE_VERSION, "priority": BACKFILL_PRIORITY,
            "limit": limit, **dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 2500:
        raise SystemExit("limit 1..2500")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("expected_authorized_postgres_runtime")
    print(json.dumps(execute(args.tenant, args.limit, args.apply), sort_keys=True))


if __name__ == "__main__":
    main()
