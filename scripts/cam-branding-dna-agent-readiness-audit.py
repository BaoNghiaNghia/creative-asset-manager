#!/usr/bin/env python3
"""Read-only production Branding DNA worker/queue audit; never print identifiers or secrets."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys

def main() -> int:
    from dotenv import load_dotenv
    env = Path("/etc/creative-asset-manager/production.env")
    if not env.is_file():
        print("ERROR: production env unavailable", file=sys.stderr)
        return 2
    load_dotenv(env, override=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
    from app.core.config import get_settings
    from app.core.database import create_database_engine
    from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel
    from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
    from app.modules.branding_dna.scan_readiness_service import shop_scan_readiness
    from app.modules.branding_dna.scout_session_router import online
    from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
    from sqlalchemy import select, func
    from sqlalchemy.orm import Session

    settings = get_settings()
    if not str(settings.DATABASE_URL or "").startswith("postgresql"):
        print("ERROR: not a PostgreSQL production database", file=sys.stderr)
        return 2
    engine = create_database_engine(settings)
    now = datetime.now(timezone.utc)
    print("AUDIT=BRANDING_DNA_READ_ONLY")
    print("SCAN_ENABLED=false")
    print("DISPATCH_REASON=scan_task_dispatch_not_deployed")
    with Session(engine) as session:
        tenant_counts = session.execute(
            select(BrandShopModel.tenant_id, func.count())
            .where(BrandShopModel.platform.in_(("amazon", "etsy")))
            .group_by(BrandShopModel.tenant_id)
        ).all()
        print(f"TENANTS_WITH_SHOPS={len(tenant_counts)}")
        for ordinal, (tenant, count) in enumerate(tenant_counts, 1):
            shops = shop_scan_readiness(session, tenant_id=tenant, include_queue=True)
            platforms = Counter(i["platform"] for i in shops)
            blockers = Counter(reason for i in shops for reason in i["blockers"])
            queue = Counter()
            for shop in shops:
                queue.update(shop["queue"])
            paired = session.execute(
                select(BrandScoutSessionModel.last_seen_at, RrugcScoutAgentModel.active)
                .join(RrugcScoutAgentModel,
                      (BrandScoutSessionModel.agent_id == RrugcScoutAgentModel.id)
                      & (BrandScoutSessionModel.tenant_id == RrugcScoutAgentModel.tenant_id))
                .where(BrandScoutSessionModel.tenant_id == tenant)
            ).all()
            from app.modules.branding_dna.worker_router import worker_online
            workers = session.scalars(
                select(BrandWorkerModel).where(BrandWorkerModel.tenant_id == tenant)
            ).all()
            paired_live = sum(1 for t, active in paired if active and online(t, now))
            worker_idle = sum(1 for x in workers if x.status == "idle" and worker_online(x, now=now))
            print(
                f"TENANT_INDEX={ordinal} SHOPS={count} AMAZON={platforms['amazon']} "
                f"ETSY={platforms['etsy']} BLOCKED={len(shops)} "
                f"QUEUED_STAGED={queue['staged']} QUEUED_PENDING={queue['pending']} "
                f"RETRY={queue['retry']} LEASED={queue['leased']} "
                f"CONNECTED_SCOUTS={paired_live} REGISTERED_WORKERS={len(workers)} "
                f"ONLINE_IDLE_WORKERS={worker_idle}"
            )
            print("BLOCKERS=" + ",".join(f"{name}:{total}" for name,total in sorted(blockers.items())))
    engine.dispose()
    print("UNSAFE_JOB_DISPATCH_ATTEMPTED=false")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
