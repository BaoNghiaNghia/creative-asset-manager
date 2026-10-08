"""Resumable Stage 0 trademark recheck using ORIGINAL keywords (without +hat).

This job refreshes only Trademark columns and raw trademark evidence; it must
never overwrite the previously saved Google Ads metrics, which use keyword+hat.
Any historical trademark result lacking exact-query provenance is stale.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, engine
from app.modules.realistic_review_ugc.keyword_volume import (
    KeywordVolumeError, RrugcKeywordVolumeService,
    restore_cached_provider_trademark, trademark_evidence_is_current,
)
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel

LOG = logging.getLogger("rrugc_trademark_backfill")


def count_statuses(session: Session) -> dict[str, int]:
    rows = session.scalars(select(RrugcKeywordVolumeModel)).all()
    return dict(sorted(Counter(
        row.trademark_status if trademark_evidence_is_current(row) else "unverified"
        for row in rows
    ).items()))


def restore_cached_evidence(session: Session, batch_size: int = 500) -> int:
    recovered = 0
    ids = session.scalars(
        select(RrugcKeywordVolumeModel.id).order_by(RrugcKeywordVolumeModel.id.asc())
    ).all()
    for offset in range(0, len(ids), batch_size):
        rows = session.scalars(select(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.id.in_(ids[offset:offset + batch_size])
        )).all()
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
    rows = session.scalars(select(RrugcKeywordVolumeModel).order_by(
        RrugcKeywordVolumeModel.tenant_id,
        RrugcKeywordVolumeModel.created_at,
        RrugcKeywordVolumeModel.id,
    )).all()
    candidates = [
        (row.tenant_id, row.keyword)
        for row in rows if not trademark_evidence_is_current(row)
    ]
    if limit is not None:
        candidates = candidates[:limit]
    # A provider outage must not resurrect old +hat statuses as TM clearance.
    for row in rows:
        if not trademark_evidence_is_current(row):
            row.trademark_status = "unverified"
            row.trademark_checked_at = None
            row.trademark_source = None
            row.trademark_match_count = None
    session.commit()

    success = failed = pending = 0
    consecutive_errors = 0
    service = RrugcKeywordVolumeService(session)
    # Process per tenant; never combine keywords across tenants.
    tenant_batches: dict[str, list[str]] = {}
    for tenant, keyword in candidates:
        tenant_batches.setdefault(tenant, []).append(keyword)
    for tenant, keywords in tenant_batches.items():
        for i in range(0, len(keywords), batch_size):
            chunk = keywords[i:i + batch_size]
            try:
                verified, requested = await service.refresh_trademark(
                    tenant_id=tenant, keywords=chunk,
                )
                success += verified
                pending += requested - verified
                consecutive_errors = 0
                LOG.info(
                    "tm_original_keyword_batch tenant=%s processed=%s/%s verified=%s pending=%s",
                    tenant[:16], success + failed + pending,
                    len(candidates), verified, requested - verified,
                )
            except (KeywordVolumeError, ValueError) as exc:
                session.rollback()
                failed += len(chunk)
                consecutive_errors += 1
                LOG.warning(
                    "tm_original_keyword_provider_error count=%s type=%s",
                    len(chunk), type(exc).__name__,
                )
                if consecutive_errors >= 3:
                    LOG.error("tm_original_keyword_circuit_breaker consecutive_failures=3")
                    return success, failed, pending
            if pause_seconds:
                await asyncio.sleep(pause_seconds)
    return success, failed, pending


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--pause", type=float, default=0.7)
    parser.add_argument("--limit", type=int, default=0, help="0 = all")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50 or args.pause < 0 or args.limit < 0:
        parser.error("batch-size must be 1..50; pause and limit cannot be negative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SessionLocal() as session:
        LOG.info("tm_original_keyword_initial %s", count_statuses(session))
        recovered = restore_cached_evidence(session)
        LOG.info("tm_original_keyword_cached_recovered=%s counts=%s", recovered, count_statuses(session))
        if args.fetch:
            verified, failed, pending = await fetch_missing(
                session, batch_size=args.batch_size, pause_seconds=args.pause,
                limit=args.limit or None,
            )
            LOG.info(
                "tm_original_keyword_provider_complete verified=%s failed=%s pending=%s",
                verified, failed, pending,
            )
        LOG.info("tm_original_keyword_final %s", count_statuses(session))
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
