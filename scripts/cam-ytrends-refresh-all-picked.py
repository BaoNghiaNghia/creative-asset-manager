#!/usr/bin/env python3
"""Refresh every completed Picked YTrends table, including historical drafts.

Read-only unless --apply. Queries the actual Etsy MCP, never generates AI copy
or marks keyword metrics as TikTok Shop search volumes. Deduplicates titles
within the run and runs no more than three concurrent sets to respect the MCP.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
import os
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.etsy_spy.model import EtsySpyPicked
from app.modules.etsy_spy.tiktok_skill import JOB_TYPE, attach_ytrends_evidence
from app.modules.etsy_spy.ytrends_mcp import (
    merge_keyword_evidence, research_etsy_keyword_set,
)
from app.modules.processing.model import ProcessingJobModel

RESEARCH_REVISION = "full-picked-consistent-research-20261112-v3"


def verified_count(evidence: Any) -> int:
    rows = evidence.get("keywords") if isinstance(evidence, dict) else None
    return sum(1 for row in rows or [] if isinstance(row, dict)
               and row.get("status") == "connected"
               and type(row.get("listings")) is int
               and isinstance(row.get("as_of_date"), str)
               and bool(row["as_of_date"]))


async def execute(tenant: str, limit: int, apply: bool, concurrency: int) -> dict:
    settings = get_settings()
    if not settings.ETSY_TIKTOK_YTRENDS_ENABLED:
        raise SystemExit("ytrends_disabled")
    counts = Counter()
    with SessionLocal() as db:
        ids = list(db.scalars(select(EtsySpyPicked.listing_key).where(
            EtsySpyPicked.tenant_id == tenant)).all())
        counts["picked_total"] = len(ids)
        if not ids:
            return {"mode": "apply" if apply else "dry_run", **counts}
        jobs = list(db.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant,
            ProcessingJobModel.job_type == JOB_TYPE,
            ProcessingJobModel.entity_id.in_(ids),
        ).order_by(ProcessingJobModel.entity_id,
                   ProcessingJobModel.created_at.desc(),
                   ProcessingJobModel.id.desc())).all())
        counts["jobs_total"] = len(jobs)
        selected = []
        seen = set()
        for job in jobs:
            kind = "latest" if job.entity_id not in seen else "history"
            seen.add(job.entity_id)
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            if job.status != "completed" or job.cancellation_requested or not isinstance(
                    payload.get("result"), dict):
                counts["not_completed"] += 1
                continue
            title = payload.get("source_title")
            if not isinstance(title, str) or not title.strip():
                counts["missing_source_title"] += 1
                continue
            counts["eligible_"+kind] += 1
            selected.append((job.id, job.entity_id, title, kind))
    selected = selected[:limit]
    counts["selected_jobs"] = len(selected)
    counts["unique_source_titles"] = len(set(title for _,_,title,_ in selected))
    if not apply or not selected:
        return {"mode": "dry_run", "tenant": tenant, "limit": limit, **dict(counts)}

    # The first query of each title is from the live MCP in this fresh process.
    # Reused titles share precisely the same evidence, preventing repeated jobs
    # from seeing inconsistent numbers within one full backfill.
    semaphore = asyncio.Semaphore(concurrency)
    token = settings.ETSY_TIKTOK_YTRENDS_BEARER_TOKEN.get_secret_value()

    async def research(title: str) -> tuple[str, dict]:
        async with semaphore:
            try:
                found = await research_etsy_keyword_set(
                    title, enabled=True, bearer_token=token,
                    timeout_seconds=settings.ETSY_TIKTOK_YTRENDS_TIMEOUT_SECONDS,
                )
            except Exception:
                from app.modules.etsy_spy.ytrends_mcp import empty_evidence
                found = empty_evidence("")
            return title, found

    titles = sorted(set(title for _,_,title,_ in selected))
    discovered = dict(await asyncio.gather(*(research(title) for title in titles)))
    for job_id, listing_key, title, kind in selected:
        fresh = discovered[title]
        counts["provider_"+str(fresh.get("status", "unavailable"))] += 1
        if not isinstance(fresh.get("keywords"), list):
            counts["no_safe_keyword_candidates"] += 1
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
            old_draft = payload.get("result")
            if not isinstance(old_draft, dict) or payload.get("source_title") != title:
                counts["concurrent_change"] += 1
                continue
            old_evidence = old_draft.get("ytrends_evidence")
            merged = merge_keyword_evidence(old_evidence, fresh)
            if merged.get("status") not in ("connected", "no_data"):
                counts["no_verified_evidence_kept"] += 1
                continue
            if not isinstance(merged.get("keywords"), list):
                counts["no_table_kept"] += 1
                continue
            merged["research_revision"] = RESEARCH_REVISION
            previous_metrics = verified_count(old_evidence)
            current_metrics = verified_count(merged)
            payload["result"] = attach_ytrends_evidence(dict(old_draft), merged)
            locked.payload_json = payload
            db.commit()
            counts["updated_"+kind] += 1
            counts["verified_rows_before"] += previous_metrics
            counts["verified_rows_after"] += current_metrics
            if current_metrics > previous_metrics:
                counts["more_verified_rows"] += 1
            elif current_metrics == previous_metrics:
                counts["equal_verified_rows"] += 1
            else:
                counts["fewer_verified_rows"] += 1
    return {"mode": "apply", "tenant": tenant, "limit": limit,
            "concurrency": concurrency, **dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 2500 or not 1 <= args.concurrency <= 4:
        raise SystemExit("limit 1..2500, concurrency 1..4")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("expected_authorized_postgres_runtime")
    print(json.dumps(asyncio.run(execute(
        args.tenant, args.limit, args.apply, args.concurrency)), sort_keys=True))


if __name__ == "__main__":
    main()
