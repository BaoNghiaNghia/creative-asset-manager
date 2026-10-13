#!/usr/bin/env python3
"""Safe automatic Etsy/Amazon Stage 0 source-image enrichment.

Dry-run is default. --apply performs up to N bounded, exact-listing image
lookups; never changes picks, trademarks or AEBrowse keyword data.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.marketplace_images import enrich_missing_marketplace_images
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel
from sqlalchemy import select


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--tenant", default=None)
    args=parser.parse_args()
    with SessionLocal() as session:
        if not args.apply:
            rows=session.scalars(select(RrugcKeywordVolumeModel).where(
                RrugcKeywordVolumeModel.source_image_url.is_(None),
                *([RrugcKeywordVolumeModel.tenant_id == args.tenant] if args.tenant else [])
            )).all()
            pending=sum((r.provider_raw_json or {}).get("research_source") in ("etsy","amazon") for r in rows)
            print(json.dumps({"mode":"dry-run","pending_primary_source_rows":pending,"missing_image_rows":len(rows)}))
            return
        result=asyncio.run(enrich_missing_marketplace_images(
            session, tenant_id=args.tenant, limit=max(0,min(8,args.limit)),
        ))
        session.commit()
        print(json.dumps({"mode":"apply", **result}))


if __name__ == "__main__":
    main()
