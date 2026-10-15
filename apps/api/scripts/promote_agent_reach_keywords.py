#!/usr/bin/env python3
"""Replay already synced Agent-Reach results without new search/provider calls.

Run with the root-owned production configuration through the sanctioned
production_env.py launcher. Default is dry-run; --apply mutates only eligible,
pending signals from a dedicated tenant-matched Agent-Reach Scout identity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
from app.modules.realistic_review_ugc.research_discovery import AGENT_NAME
from app.modules.realistic_review_ugc.research_intelligence import backfill_auto_discover_candidates
from app.modules.realistic_review_ugc.research_vision import enqueue_research_vision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Import conservative quotes into Stage 0")
    args = parser.parse_args()
    with SessionLocal() as session:
        tenants = session.scalars(select(RrugcScoutAgentModel.tenant_id).where(
            RrugcScoutAgentModel.name == AGENT_NAME,
        ).distinct()).all()
    totals = {"tenants": len(tenants), "checked": 0, "auto_promoted": 0, "needs_review": 0, "vision_queued": 0}
    for tenant_id in tenants:
        with SessionLocal() as session:
            result = backfill_auto_discover_candidates(
                session, tenant_id=tenant_id, dry_run=not args.apply
            )
            for key in ("checked", "auto_promoted", "needs_review"):
                totals[key] += result[key]
            if args.apply:
                totals["vision_queued"] += enqueue_research_vision(session, tenant_id)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **totals}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
