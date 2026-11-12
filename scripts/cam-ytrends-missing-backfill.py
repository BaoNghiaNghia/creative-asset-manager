#!/usr/bin/env python3
"""Only fill missing YTrends keyword research in completed Etsy Picked listing drafts.

Dry-run by default; never generates AI copy, alters listing content or publishes.
Launch inside the authorized CAM API runtime with DATABASE_URL already present.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.etsy_spy.tiktok_skill import JOB_TYPE, attach_ytrends_evidence
from app.modules.etsy_spy.ytrends_mcp import research_etsy_keyword_set
from app.modules.processing.model import ProcessingJobModel


def needs_research(result: dict) -> bool:
    evidence = result.get("ytrends_evidence")
    if not isinstance(evidence, dict):
        return True
    rows = evidence.get("keywords")
    return not isinstance(rows, list) or len(rows) < 3


async def backfill(tenant: str, limit: int, apply: bool) -> dict:
    result = Counter()
    settings = get_settings()
    if not settings.ETSY_TIKTOK_YTRENDS_ENABLED:
        raise SystemExit("ytrends_is_disabled")
    # One Picked listing can have several generated drafts. Only its latest
    # completed, noncancelled job is eligible (not an obsolete generation).
    with SessionLocal() as db:
        ids = list(db.scalars(
            select(EtsySpyPicked.listing_key).where(
                EtsySpyPicked.tenant_id == tenant
            ).order_by(EtsySpyPicked.picked_at.asc())
        ).all())
    result["picked_scanned"] = len(ids)
    processed = 0
    for listing_key in ids:
        if processed >= limit:
            break
        with SessionLocal() as db:
            job = db.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == tenant,
                ProcessingJobModel.job_type == JOB_TYPE,
                ProcessingJobModel.entity_id == listing_key,
            ).order_by(ProcessingJobModel.created_at.desc(),
                       ProcessingJobModel.id.desc()).limit(1))
            if job is None or job.status != "completed" or job.cancellation_requested:
                result["not_completed"] += 1
                continue
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            draft = payload.get("result")
            if not isinstance(draft, dict) or not needs_research(draft):
                result["already_complete"] += 1
                continue
            title = payload.get("source_title")
            if not isinstance(title, str) or not title.strip():
                result["source_title_missing"] += 1
                continue
            job_id = job.id
        processed += 1
        result["eligible"] += 1
        if not apply:
            continue
        try:
            evidence = await research_etsy_keyword_set(
                title, enabled=True,
                bearer_token=settings.ETSY_TIKTOK_YTRENDS_BEARER_TOKEN.get_secret_value(),
                timeout_seconds=settings.ETSY_TIKTOK_YTRENDS_TIMEOUT_SECONDS,
            )
        except Exception:
            result["provider_error"] += 1
            continue
        if evidence.get("status") != "connected":
            result["no_verified_evidence"] += 1
            continue
        with SessionLocal() as db:
            locked = db.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job_id,
                ProcessingJobModel.tenant_id == tenant,
                ProcessingJobModel.job_type == JOB_TYPE,
                ProcessingJobModel.status == "completed",
                ProcessingJobModel.cancellation_requested.is_(False),
            ).with_for_update(skip_locked=True))
            if locked is None:
                result["concurrent_change"] += 1
                continue
            current = dict(locked.payload_json or {})
            old = current.get("result")
            # CAS: Never override a newer draft or a concurrent research update.
            newest = db.scalar(select(ProcessingJobModel.id).where(
                ProcessingJobModel.tenant_id == tenant,
                ProcessingJobModel.job_type == JOB_TYPE,
                ProcessingJobModel.entity_id == listing_key,
            ).order_by(ProcessingJobModel.created_at.desc(),
                       ProcessingJobModel.id.desc()).limit(1))
            if newest != job_id or not isinstance(old, dict) or not needs_research(old):
                result["concurrent_change"] += 1
                continue
            current["result"] = attach_ytrends_evidence(old, evidence)
            locked.payload_json = current
            db.commit()
            result["updated"] += 1
    return {"mode": "apply" if apply else "dry_run", "tenant": tenant,
            "limit": limit, **dict(result)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        raise SystemExit("limit_must_be_between_1_and_500")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("requires_authorized_production_database_environment")
    print(json.dumps(asyncio.run(backfill(args.tenant, args.limit, args.apply))))


if __name__ == "__main__":
    main()
