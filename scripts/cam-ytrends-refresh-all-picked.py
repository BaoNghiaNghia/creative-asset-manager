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
    source_backed_keyword_count, verified_keyword_count,
)
from app.modules.processing.model import ProcessingJobModel

RESEARCH_REVISION = "full-picked-source-safe-20261116-v5"


def verified_count(evidence: Any) -> int:
    return verified_keyword_count(evidence)


async def execute(tenant: str, limit: int, apply: bool,
                  concurrency: int, offset: int = 0,
                  refresh_current: bool = False) -> dict:
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
        already_current_ids: set[str] = set()
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
            old_evidence = payload["result"].get("ytrends_evidence")
            if (not refresh_current and isinstance(old_evidence, dict)
                    and old_evidence.get("research_revision") == RESEARCH_REVISION
                    and verified_count(old_evidence) > 0):
                already_current_ids.add(job.id)
                counts["already_verified_" + kind] += 1
            selected.append((job.id, job.entity_id, title, kind))
    counts["eligible_jobs"] = len(selected)
    counts["picked_without_jobs"] = max(0, len(ids) - len(seen))
    selected = selected[offset:offset + limit]
    counts["selected_complete_jobs"] = len(selected)
    selected = [record for record in selected
                if record[0] not in already_current_ids]
    counts["selected_jobs"] = len(selected)
    counts["offset"] = offset
    counts["unique_source_titles"] = len(set(title for _,_,title,_ in selected))
    if not apply or not selected:
        return {"mode": "apply" if apply else "dry_run",
                "status": "already_current" if apply else "preview",
                "tenant": tenant, "limit": limit, **dict(counts)}

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

    # Group jobs by title: identical source titles share one bounded research
    # call. Persist each completed window immediately, so interruptions do
    # not roll back a full-catalog network prefetch.
    rows_by_title: dict[str, list[tuple]] = {}
    for record in selected:
        rows_by_title.setdefault(record[2], []).append(record)
    titles = sorted(rows_by_title)

    def persist_batch(results: dict[str, dict]) -> None:
        for title, fresh in results.items():
            for job_id, listing_key, _, kind in rows_by_title[title]:
                counts["jobs_processed"] += 1
                counts["provider_" + str(fresh.get("status", "unavailable"))] += 1
                if not isinstance(fresh.get("keywords"), list):
                    counts["no_safe_keyword_candidates"] += 1
                if not source_backed_keyword_count(fresh):
                    counts["provider_missing_verified_source_kept"] += 1
                    continue
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
                    if (not isinstance(old_draft, dict)
                            or payload.get("source_title") != title):
                        counts["concurrent_change"] += 1
                        continue
                    old_evidence = old_draft.get("ytrends_evidence")
                    merged = merge_keyword_evidence(old_evidence, fresh)
                    if not source_backed_keyword_count(merged):
                        counts["no_verified_evidence_kept"] += 1
                        continue
                    previous_metrics = verified_count(old_evidence)
                    current_metrics = verified_count(merged)
                    if current_metrics < previous_metrics:
                        counts["preserved_better_evidence"] += 1
                        continue
                    merged["research_revision"] = RESEARCH_REVISION
                    payload["result"] = attach_ytrends_evidence(
                        dict(old_draft), merged)
                    locked.payload_json = payload
                    db.commit()
                    counts["updated_" + kind] += 1
                    counts["verified_rows_before"] += previous_metrics
                    counts["verified_rows_after"] += current_metrics
                    if current_metrics > previous_metrics:
                        counts["more_verified_rows"] += 1
                    elif current_metrics == previous_metrics:
                        counts["equal_verified_rows"] += 1
                    else:
                        counts["fewer_verified_rows"] += 1
        counts["titles_processed"] += len(results)
        counts["batches_completed"] += 1

    # Sample different alphabetical regions rather than two adjacent titles.
    # Otherwise one narrow niche can falsely classify a working Etsy MCP as
    # down and prevent backfilling unrelated Picked products.
    probe_titles = list(dict.fromkeys(
        titles[index] for index in (0, len(titles) // 2, len(titles) - 1)
    ))
    probe_results = dict(await asyncio.gather(
        *(research(title) for title in probe_titles)))
    counts["provider_probe_titles"] = len(probe_titles)
    if not any(source_backed_keyword_count(item)
               for item in probe_results.values()):
        counts["provider_probe_unverified"] = len(probe_titles)
        return {"mode": "apply", "status": "provider_unavailable_no_writes",
                "tenant": tenant, "limit": limit, "offset": offset,
                "concurrency": concurrency, **dict(counts)}

    persist_batch(probe_results)
    remaining = [title for title in titles if title not in probe_results]
    window = max(1, concurrency * 3)
    consecutive_unverified_windows = 0
    for start in range(0, len(remaining), window):
        results = dict(await asyncio.gather(*(
            research(title) for title in remaining[start:start + window]
        )))
        persist_batch(results)
        if any(source_backed_keyword_count(item)
               for item in results.values()):
            consecutive_unverified_windows = 0
        else:
            consecutive_unverified_windows += 1
            # Stop a failing upstream rather than send thousands of retries.
            # Committed earlier windows remain available for the next run.
            if consecutive_unverified_windows >= 2:
                counts["provider_unverified_window_streak"] = (
                    consecutive_unverified_windows)
                return {"mode": "apply", "status": "provider_degraded_partial",
                        "tenant": tenant, "limit": limit, "offset": offset,
                        "concurrency": concurrency, **dict(counts)}

    return {"mode": "apply", "status": "completed", "tenant": tenant,
            "limit": limit, "offset": offset, "concurrency": concurrency,
            **dict(counts)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--offset", type=int, default=0,
                        help="Zero-based pagination for every completed Picked draft")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh-current", action="store_true",
                        help="Also refresh already verified V5 records")
    args = parser.parse_args()
    if (not 1 <= args.limit <= 2500 or not 1 <= args.concurrency <= 4
            or not 0 <= args.offset <= 250_000):
        raise SystemExit("limit 1..2500, concurrency 1..4, offset 0..250000")
    if not os.environ.get("DATABASE_URL", "").startswith("postgresql"):
        raise SystemExit("expected_authorized_postgres_runtime")
    print(json.dumps(asyncio.run(execute(
        args.tenant, args.limit, args.apply, args.concurrency,
        args.offset, args.refresh_current)), sort_keys=True))


if __name__ == "__main__":
    main()
