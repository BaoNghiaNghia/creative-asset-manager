"""One-time/resumable AEBrowse trademark backfill for all Stage 0 keywords.

Usage (in production API environment):
  python -m app.modules.realistic_review_ugc.trademark_backfill --fetch --batch-size 10

Phase 1 restores existing evidence from raw provider responses at zero API cost.
Phase 2 queries only unverified keyword records via the normal service with
strict rate pacing, progress logs, tenant isolation and a failure circuit break.
Run again to resume after network errors; verified results are preserved.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.modules.realistic_review_ugc.keyword_volume import (
    KeywordVolumeError,
    RrugcKeywordVolumeService,
    restore_cached_provider_trademark,
)
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel

LOG = logging.getLogger("rrugc_trademark_backfill")
VERIFIED = {"safe", "warning", "danger"}


def restore_cached_evidence(session: Session, batch_size: int = 500) -> int:
    recovered = 0
    ids = session.scalars(
        select(RrugcKeywordVolumeModel.id).where(
            ~RrugcKeywordVolumeModel.trademark_status.in_(VERIFIED)
        ).order_by(RrugcKeywordVolumeModel.id.asc())
    ).all()
    for offset in range(0, len(ids), batch_size):
        rows = session.scalars(
            select(RrugcKeywordVolumeModel).where(
                RrugcKeywordVolumeModel.id.in_(ids[offset:offset + batch_size])
            )
        ).all()
        for row in rows:
            if restore_cached_provider_trademark(row):
                recovered += 1
        session.commit()
    return recovered


async def fetch_missing(
    session: Session, *,
    batch_size: int,
    pause_seconds: float,
    limit: int | None = None,
) -> tuple[int, int, int]:
    # Stable identity snapshot prevents reprocessing missing results forever.
    candidates = session.execute(
        select(
            RrugcKeywordVolumeModel.id, RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordVolumeModel.keyword,
        ).where(
            ~RrugcKeywordVolumeModel.trademark_status.in_(VERIFIED)
        ).order_by(
            RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordVolumeModel.created_at,
            RrugcKeywordVolumeModel.id,
        ).limit(limit) if limit is not None else
        select(
            RrugcKeywordVolumeModel.id, RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordVolumeModel.keyword,
        ).where(
            ~RrugcKeywordVolumeModel.trademark_status.in_(VERIFIED)
        ).order_by(
            RrugcKeywordVolumeModel.tenant_id,
            RrugcKeywordVolumeModel.created_at,
            RrugcKeywordVolumeModel.id,
        )
    ).all()
    success = 0
    failed = 0
    stale_bundles = 0
    consecutive_errors = 0
    service = RrugcKeywordVolumeService(session)
    # Batch within a tenant, never call one tenant's keywords for another.
    tenant_batches: dict[str, list[str]] = {}
    for _id, tenant_id, keyword in candidates:
        tenant_batches.setdefault(tenant_id, []).append(keyword)
    for tenant_id, keywords in tenant_batches.items():
        for pos in range(0, len(keywords), batch_size):
            chunk = keywords[pos:pos + batch_size]
            try:
                result = await service.resolve(
                    tenant_id=tenant_id, keywords=chunk, force=True,
                )
                verified = sum(
                    row.trademark_status in VERIFIED for row in result.rows
                )
                success += verified
                missing = len(chunk) - verified
                stale_bundles += missing
                consecutive_errors = 0
                LOG.info(
                    "tm_backfill_batch tenant=%s processed=%s/%s verified=%s pending=%s",
                    tenant_id[:16], success + stale_bundles + failed,
                    len(candidates), verified, missing,
                )
            except (KeywordVolumeError, ValueError) as exc:
                session.rollback()
                failed += len(chunk)
                consecutive_errors += 1
                LOG.warning(
                    "tm_backfill_provider_error count=%s type=%s",
                    len(chunk), type(exc).__name__,
                )
                if consecutive_errors >= 3:
                    LOG.error("tm_backfill_circuit_breaker after=3 consecutive failures")
                    return success, failed, stale_bundles
            if pause_seconds:
                await asyncio.sleep(pause_seconds)
    return success, failed, stale_bundles


def count_statuses(session: Session) -> dict[str, int]:
    values = Counter(session.scalars(
        select(RrugcKeywordVolumeModel.trademark_status)
    ).all())
    return dict(sorted(values.items()))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true", help="Fetch missing TM statuses from AEBrowse.")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--pause", type=float, default=0.7)
    parser.add_argument("--limit", type=int, default=0, help="0 = all")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50:
        parser.error("--batch-size must be 1..50")
    if args.pause < 0 or args.limit < 0:
        parser.error("--pause and --limit must be non-negative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SessionLocal() as session:
        LOG.info("tm_backfill_initial %s", count_statuses(session))
        recovered = restore_cached_evidence(session)
        LOG.info("tm_backfill_cached_recovered=%s counts=%s", recovered, count_statuses(session))
        if args.fetch:
            success, failed, incomplete = await fetch_missing(
                session, batch_size=args.batch_size,
                pause_seconds=args.pause,
                limit=args.limit or None,
            )
            LOG.info(
                "tm_backfill_provider_complete newly_verified=%s failed=%s missing_tm=%s",
                success, failed, incomplete,
            )
        LOG.info("tm_backfill_final %s", count_statuses(session))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
