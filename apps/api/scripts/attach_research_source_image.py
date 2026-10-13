#!/usr/bin/env python3
"""Link an explicitly user-supplied CDN photo to one existing Stage 0 listing.

Default is dry-run. Match keyword, marketplace source, and a single recorded
listing. Aborts on ambiguity, existing images or any cross-tenant collision.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel,RrugcKeywordAgentOriginModel
from app.modules.realistic_review_ugc.stage0_source_images import attach_marketplace_image


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--platform", choices=("etsy","amazon"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--apply", action="store_true")
    options=parser.parse_args()
    with SessionLocal() as db:
        rows=db.scalars(select(RrugcKeywordVolumeModel).where(
            RrugcKeywordVolumeModel.keyword.ilike(options.keyword)
        )).all()
        candidates=[]
        for row in rows:
            origins=db.scalars(select(RrugcKeywordAgentOriginModel).where(
                RrugcKeywordAgentOriginModel.tenant_id==row.tenant_id,
                RrugcKeywordAgentOriginModel.keyword_normalized==row.keyword_normalized,
                RrugcKeywordAgentOriginModel.source_platform==options.platform,
                RrugcKeywordAgentOriginModel.source_url.is_not(None),
            )).all()
            for origin in origins:
                candidates.append((row,origin))
        distinct={(r.id,o.source_url):(r,o) for r,o in candidates}
        if len(distinct)!=1:
            print(json.dumps({"status":"ambiguous_or_missing","matches":len(distinct),"applied":False}))
            return 2
        row,origin=next(iter(distinct.values()))
        if row.source_image_url:
            print(json.dumps({"status":"existing_image_preserved","applied":False}))
            return 0
        if not options.apply:
            print(json.dumps({"status":"matched","source":options.platform,"listing":origin.source_url,
                              "keyword":row.keyword,"applied":False}))
            return 0
        updated=asyncio.run(attach_marketplace_image(
            db,tenant_id=row.tenant_id,keyword_id=row.id,
            listing_url=origin.source_url,image_url=options.image,
            actor_id="operator:user-provided-example",
        ))
        print(json.dumps({"status":"saved","keyword":updated.keyword,"source":options.platform,
                          "image_url":updated.source_image_url,"applied":True}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
