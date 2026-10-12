"""One-time, resumable Stage 5 copy refresh from the Etsy-8869 style profile.

No AI calls, source file reads, image regenerations, score changes or edits to
hidden editorial rejections. Safe to dry-run before applying in production.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcStage3AnalysisModel
from app.modules.realistic_review_ugc.stage3 import REVIEW_COPY_STYLE_VERSION, compose_review_text

LOG = logging.getLogger(__name__)


def refresh_existing_review_copy(
    session: Session, *, apply: bool = False, limit: int = 10000,
) -> dict[str, object]:
    if not 1 <= limit <= 100000:
        raise ValueError("limit must be between 1 and 100000")
    rows = list(session.scalars(select(RrugcStage3AnalysisModel).where(
        RrugcStage3AnalysisModel.status.in_(("ready", "rejected")),
        RrugcStage3AnalysisModel.review_text.is_not(None),
    ).order_by(
        RrugcStage3AnalysisModel.tenant_id.asc(),
        RrugcStage3AnalysisModel.created_at.asc(),
        RrugcStage3AnalysisModel.id.asc(),
    ).limit(limit)))
    # Track only concepts within the same tenant. Never include actual
    # customer-supplied text from the style workbook.
    seen: dict[str, set[str]] = defaultdict(set)
    count = 0
    changed = 0
    now = datetime.now(timezone.utc)
    for row in rows:
        count += 1
        existing = seen[row.tenant_id]
        candidate = None
        for variant in range(512):
            text = compose_review_text(
                analysis_id=row.id, embroidery_visible=bool(row.embroidery_visible),
                variant=variant,
            )
            if text not in existing:
                candidate = text
                break
        if candidate is None:
            raise RuntimeError("Review concept vocabulary exhausted")
        existing.add(candidate)
        if candidate != row.review_text:
            changed += 1
            if apply:
                row.review_text = candidate
                row.review_generated_at = now
    if apply:
        session.commit()
    return {
        "style_version": REVIEW_COPY_STYLE_VERSION,
        "scanned": count,
        "rewritten": changed,
        "applied": apply,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rewrite synthetic Stage 5 concepts without re-analyzing images.")
    parser.add_argument("--apply", action="store_true", help="Persist; otherwise count-only dry-run")
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    with SessionLocal() as session:
        result = refresh_existing_review_copy(session, apply=args.apply, limit=args.limit)
    LOG.info("stage5_review_copy_refresh", extra=result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
