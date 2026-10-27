"""Read-only guard before updating a pinned Slot 1 Codex Skill in-place.

Existing queued/running jobs use a bundle SHA256 pinned at creation. Updating
the installed bundle without checking them would cause ecommerce_skill_changed.
Never alter job rows in this diagnostic.
"""
from __future__ import annotations

import argparse
import json
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)

TENANT = "creative-assets-prod"
STAGE = "prod_s2_ec1"
SKILL = "gatorhats-8869-ecommerce-front-thread-v3"
TERMINAL = ("completed", "failed", "cancelled", "canceled")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-clear", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as session:
        assignment = session.get(RrugcStageSkillDefaultModel, (TENANT, STAGE))
        selected = (
            session.get(RrugcStage2SkillRegistryModel, assignment.registry_id)
            if assignment and assignment.registry_id else None
        )
        counts = session.execute(
            select(
                RrugcEcommerceJobModel.status,
                RrugcEcommerceJobModel.skill_bundle_sha256,
                func.count(),
            ).where(
                RrugcEcommerceJobModel.tenant_id == TENANT,
                RrugcEcommerceJobModel.stage == STAGE,
                RrugcEcommerceJobModel.skill_name == SKILL,
                RrugcEcommerceJobModel.status.notin_(TERMINAL),
            ).group_by(
                RrugcEcommerceJobModel.status,
                RrugcEcommerceJobModel.skill_bundle_sha256,
            )
        ).all()
        pending = [
            {"status": status, "count": int(count), "pin_prefix": (digest or "")[:12]}
            for status, digest, count in counts
        ]
        safe = selected is not None and selected.skill_name == SKILL and not pending
        print(json.dumps({
            "slot": STAGE,
            "assigned_skill": selected.skill_name if selected else None,
            "outstanding_v3_jobs": sum(row["count"] for row in pending),
            "pending_by_status": pending,
            "in_place_update_safe": safe,
        }, sort_keys=True))
        if args.require_clear and not safe:
            raise SystemExit("Slot 1 has outstanding pinned v3 jobs or assignment changed")


if __name__ == "__main__":
    main()
