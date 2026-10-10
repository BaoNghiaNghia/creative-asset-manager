"""Install verified Stage 1 Six Designs Skill and migrate only legacy-v4 defaults.

Dry-run by default. Do not alter v4 skill (other stages use it), jobs, or
historical output files. Applies only when one existing tenant explicitly
uses v4 as Stage 1 default. Records an audit row.
"""
from __future__ import annotations
import argparse
import io
import zipfile
from pathlib import Path
from sqlalchemy import select

from app.core.database import SessionLocal
from app.modules.realistic_review_ugc.model import RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.stage2_skills import (
    inspect_uploaded_stage2_skill_bundle, install_local_stage2_skill_bundle,
)
from app.modules.realistic_review_ugc.skill_registry import reconcile_skill_registry

NEW = "gatorhats-stage1-six-designs"
LEGACY = "hanh-redesign-8869-ver-4"
SKILL_PATH = Path(__file__).resolve().parents[2] / "managed_skills" / NEW


def eligible_tenant(session):
    rows = list(session.execute(
        select(RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel)
        .join(RrugcStage2SkillRegistryModel,
              RrugcStageSkillDefaultModel.registry_id == RrugcStage2SkillRegistryModel.id)
        .where(RrugcStageSkillDefaultModel.stage == "stage1",
               RrugcStage2SkillRegistryModel.skill_name == LEGACY,
               RrugcStage2SkillRegistryModel.tenant_id == RrugcStageSkillDefaultModel.tenant_id)
    ))
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one tenant still using legacy Stage 1 v4; refusing default migration")
    return rows[0]


def skill_bundle():
    with io.BytesIO() as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for filename in ("SKILL.md", "manifest.json"):
                archive.write(SKILL_PATH / filename, filename)
        payload = stream.getvalue()
    bundle = inspect_uploaded_stage2_skill_bundle(payload)
    if bundle.skill_name != NEW:
        raise RuntimeError("Stage 1 package name mismatch")
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    bundle = skill_bundle()
    with SessionLocal() as session:
        stage, old = eligible_tenant(session)
        print(f"mode={'apply' if args.apply else 'dry-run'} old_skill={old.skill_name} new_skill={NEW} bundle_sha256={bundle.bundle_sha256}")
        if not args.apply:
            print("No changes; history and other stage defaults unchanged.")
            return

    installed = install_local_stage2_skill_bundle(bundle, replace=False)
    with SessionLocal() as session:
        stage, old = eligible_tenant(session)
        tenant = stage.tenant_id
        reconcile_skill_registry(session, tenant_id=tenant, refresh=True)
        new_row = session.scalar(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == tenant,
            RrugcStage2SkillRegistryModel.source == "local",
            RrugcStage2SkillRegistryModel.skill_name == NEW,
            RrugcStage2SkillRegistryModel.enabled.is_(True),
            RrugcStage2SkillRegistryModel.deleted_at.is_(None),
            RrugcStage2SkillRegistryModel.validation_status == "valid",
        ))
        if new_row is None or not installed.ready:
            raise RuntimeError("Installed Six Designs Skill unavailable for the tenant")
        # This is only a Stage 1 default change. Stage 2/4 and legacy v4 files remain intact.
        stage.registry_id = new_row.id
        session.add(AuthAuditEventModel(
            tenant_id=tenant, actor_id="operator-stage1-six-designs",
            action="rrugc.stage_skill.default_updated",
            detail_json={"stage": "stage1", "old_skill": LEGACY,
                         "new_skill": NEW, "registry_id": new_row.id,
                         "reason": "strict_six_final_design_contract"},
        ))
        session.commit()
        session.refresh(stage)
        if stage.registry_id != new_row.id:
            raise RuntimeError("Stage 1 default verification failed")
    print(f"SUCCESS: Stage 1 default switched to {NEW}; all existing versions preserved.")


if __name__ == "__main__":
    main()
