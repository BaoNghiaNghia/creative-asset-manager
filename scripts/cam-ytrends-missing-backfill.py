#!/usr/bin/env python3
"""Idempotent source-backed YTrends backfill for latest AND historical Picked drafts.

Read-only dry-run is default. No AI, Etsy scraping, draft regeneration or publish.
The existing title/highlights/description/fit content remains unchanged.
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

RESEARCH_REVISION = "normalized-title-20261112-v2"


def evidence_quality(evidence: dict | None) -> tuple[int, int]:
    if not isinstance(evidence, dict):
        return (0, 0)
    rows = evidence.get("keywords")
    if not isinstance(rows, list):
        return (0, 0)
    verified = sum(1 for row in rows if isinstance(row, dict)
                   and isinstance(row.get("listings"), int)
                   and not isinstance(row.get("listings"), bool)
                   and row.get("as_of_date"))
    return (verified, len(rows))


def needs_research(draft: dict) -> bool:
    evidence = draft.get("ytrends_evidence")
    return not (isinstance(evidence, dict)
                and evidence.get("research_revision") == RESEARCH_REVISION
                and evidence_quality(evidence)[1] >= 3)


async def backfill(tenant: str, limit: int, apply: bool, include_history: bool) -> dict:
    counts = Counter()
    settings = get_settings()
    if not settings.ETSY_TIKTOK_YTRENDS_ENABLED:
        raise SystemExit("ytrends_is_disabled")
    with SessionLocal() as db:
        ids = list(db.scalars(select(EtsySpyPicked.listing_key).where(
            EtsySpyPicked.tenant_id == tenant).order_by(
            EtsySpyPicked.picked_at.asc(), EtsySpyPicked.listing_key.asc())).all())
        jobs = list(db.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant,
            ProcessingJobModel.job_type == JOB_TYPE,
            ProcessingJobModel.entity_id.in_(ids),
        ).order_by(ProcessingJobModel.entity_id, ProcessingJobModel.created_at.desc(),
                   ProcessingJobModel.id.desc())).all())
    counts["picked_scanned"] = len(ids)
    counts["jobs_scanned"] = len(jobs)
    seen = set()
    pending = []
    for job in jobs:
        is_latest = job.entity_id not in seen
        seen.add(job.entity_id)
        if not is_latest and not include_history:
            continue
        version = "latest" if is_latest else "history"
        if job.status != "completed" or job.cancellation_requested:
            counts["not_completed_"+version] += 1
            continue
        payload = job.payload_json if isinstance(job.payload_json, dict) else {}
        draft = payload.get("result")
        if not isinstance(draft, dict):
            counts["no_result_"+version] += 1
            continue
        if not needs_research(draft):
            counts["already_checked_"+version] += 1
            continue
        title = payload.get("source_title")
        if not isinstance(title, str) or not title.strip():
            counts["missing_title_"+version] += 1
            continue
        pending.append((job.id, job.entity_id, title, version))
    counts["eligible_total"] = len(pending)
    counts["selected"] = min(limit, len(pending))
    if not apply:
        return {"mode": "dry_run", "tenant": tenant,
                "include_history": include_history, "limit": limit, **dict(counts)}
    for job_id, listing_key, title, version in pending[:limit]:
        try:
            evidence = await research_etsy_keyword_set(
                title, enabled=True,
                bearer_token=settings.ETSY_TIKTOK_YTRENDS_BEARER_TOKEN.get_secret_value(),
                timeout_seconds=settings.ETSY_TIKTOK_YTRENDS_TIMEOUT_SECONDS,
            )
        except Exception:
            counts["provider_error"] += 1
            continue
        rows = evidence.get("keywords")
        if not isinstance(rows, list) or len(rows) < 3:
            counts["no_safe_queries"] += 1
            continue
        if not any(isinstance(row, dict) and row.get("status") in ("connected", "no_data")
                   for row in rows):
            counts["provider_unavailable"] += 1
            continue
        evidence["research_revision"] = RESEARCH_REVISION
        with SessionLocal() as db:
            locked = db.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.id == job_id,
                ProcessingJobModel.tenant_id == tenant,
                ProcessingJobModel.job_type == JOB_TYPE,
                ProcessingJobModel.entity_id == listing_key,
                ProcessingJobModel.status == "completed",
                ProcessingJobModel.cancellation_requested.is_(False),
            ).with_for_update(skip_locked=True))
            if locked is None:
                counts["concurrent_change"] += 1
                continue
            payload = dict(locked.payload_json or {})
            draft = payload.get("result")
            if not isinstance(draft, dict) or not needs_research(draft):
                counts["concurrent_change"] += 1
                continue
            old_evidence = draft.get("ytrends_evidence")
            old_quality = evidence_quality(old_evidence)
            new_quality = evidence_quality(evidence)
            if new_quality < old_quality:
                counts["preserved_better_evidence"] += 1
                continue
            # Never alter any other field of the generated draft.
            payload["result"] = attach_ytrends_evidence(dict(draft), evidence)
            locked.payload_json = payload
            db.commit()
            counts["updated_"+version] += 1
            if new_quality[0] > old_quality[0]:
                counts["more_verified_rows"] += 1
            elif new_quality[0] == 0:
                counts["query_rows_without_verified_metrics"] += 1
    return {"mode": "apply", "tenant": tenant, "include_history": include_history,
            "limit": limit, **dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--include-history", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        raise SystemExit("limit_must_be_between_1_and_500")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("requires_authorized_production_database_environment")
    print(json.dumps(asyncio.run(backfill(
        args.tenant, args.limit, args.apply, args.include_history))))


if __name__ == "__main__":
    main()
