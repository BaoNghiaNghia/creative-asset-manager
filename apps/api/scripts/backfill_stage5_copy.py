#!/usr/bin/env python3
"""One-time, safe Stage 5 copy backfill; defaults to dry-run.

Updates ONLY ready reviews whose text exactly matches the former v1
automatic composer. Does not recreate Stage 4 images, rerun Gemini, change
the reviewer identity, modify the star rating, or unhide editorial rejections.
Idempotent: current v2 output will not be rewritten on subsequent runs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcStage3AnalysisModel
from app.modules.realistic_review_ugc.stage3 import (
    REVIEW_COPY_STYLE_VERSION,
    refresh_legacy_stage5_copies,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Commit eligible copy updates")
    parser.add_argument("--tenant", default=None, help="Restrict to one tenant (default: all)")
    args = parser.parse_args()
    with SessionLocal() as session:
        stmt = select(RrugcStage3AnalysisModel).where(
            RrugcStage3AnalysisModel.review_text.is_not(None)
        )
        if args.tenant:
            stmt = stmt.where(RrugcStage3AnalysisModel.tenant_id == args.tenant)
        stmt = stmt.order_by(
            RrugcStage3AnalysisModel.tenant_id,
            RrugcStage3AnalysisModel.created_at,
            RrugcStage3AnalysisModel.id,
        )
        rows = list(session.scalars(stmt))
        summary = refresh_legacy_stage5_copies(rows, dry_run=not args.apply)
        if args.apply and summary["changed"]:
            session.commit()
        else:
            session.rollback()
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "style_version": REVIEW_COPY_STYLE_VERSION,
        **summary,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
