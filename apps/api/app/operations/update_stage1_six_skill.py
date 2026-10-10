"""Safely refresh installed Stage 1 six-designs Skill instructions.

Read-only by default. Refuse modifying Skill files if any related job is
queued/running, preserving pinned bundle SHA semantics. Do not touch Stage 2/4.
"""
from __future__ import annotations
import argparse
from sqlalchemy import select, func

from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcKeywordImageJobModel, RrugcStage2SkillRegistryModel
from app.modules.realistic_review_ugc.stage2_skills import install_local_stage2_skill_bundle
from app.modules.realistic_review_ugc.skill_registry import reconcile_skill_registry
from app.operations.install_stage1_six_skill import NEW, skill_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    bundle = skill_bundle()
    with SessionLocal() as session:
        registry = list(session.scalars(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.skill_name == NEW,
            RrugcStage2SkillRegistryModel.source == "local",
            RrugcStage2SkillRegistryModel.deleted_at.is_(None),
        )))
        if len(registry) != 1:
            raise RuntimeError("Expected exactly one installed Stage 1 skill registry")
        active = int(session.scalar(select(func.count()).select_from(RrugcKeywordImageJobModel).where(
            RrugcKeywordImageJobModel.skill_name == NEW,
            RrugcKeywordImageJobModel.status.in_(("queued", "running")),
        )) or 0)
        if active:
            raise RuntimeError(f"Skill currently used by {active} pending/running jobs; update postponed")
        tenant = registry[0].tenant_id
    print(f"mode={'apply' if args.apply else 'dry-run'} skill={NEW} active_jobs=0")
    if not args.apply:
        return
    installed = install_local_stage2_skill_bundle(bundle, replace=True)
    if not installed.ready:
        raise RuntimeError("Updated Stage 1 six-designs Skill did not install correctly")
    with SessionLocal() as session:
        reconcile_skill_registry(session, tenant_id=tenant, refresh=True)
        session.commit()
    print(f"SUCCESS: refreshed {NEW} to {installed.local_version} without changing other stages.")


if __name__ == "__main__":
    main()
