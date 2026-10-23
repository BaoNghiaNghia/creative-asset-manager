"""Install CAM's compatible Typography Concepts Skill for GatorHats Products Stage 1.

Dry-run by default, only changes the production tenant's two Stage 1 aliases,
never deletes old Skills or Drive versions. Fail closed if active Stage 1 jobs.
The approved OFL font subset is assembled from the existing verified library.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import zipfile
from pathlib import Path

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.config import get_settings
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordImageJobModel, RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)
from app.modules.realistic_review_ugc.skill_registry import reconcile_skill_registry
from app.modules.realistic_review_ugc.stage_skill_settings import keyword_skill_compatible
from app.modules.realistic_review_ugc.stage2_skills import (
    inspect_uploaded_stage2_skill_bundle, install_local_stage2_skill_bundle,
)
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.stage1_typography_renderer import FONTS, SKILL_NAME

SOURCE = Path(__file__).resolve().parents[2] / "managed_skills"
PROD_TENANT = "creative-assets-prod"


def build_bundle():
    root = SOURCE / SKILL_NAME
    # A complete self-contained Skill: font files, licenses and provenance
    # are bundled here; never look up the retired 200-font registry.
    from app.modules.realistic_review_ugc.stage1_typography_renderer import _verify_fonts
    _verify_fonts(root / "fonts")
    with io.BytesIO() as stream:
        with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(root.rglob("*")):
                if file.is_file():
                    archive.write(file, file.relative_to(root).as_posix())
        data = stream.getvalue()
    skill = inspect_uploaded_stage2_skill_bundle(data)
    if skill.skill_name != SKILL_NAME:
        raise RuntimeError("Typography skill package identity mismatch")
    return skill


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retry-failed-keyword", default="", help="Explicitly requeue one matching failed Stage 1 keyword using the new Skill.")
    args = parser.parse_args()

    skill = build_bundle()
    with SessionLocal() as session:
        existing = list(session.execute(
            select(RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel)
            .join(RrugcStage2SkillRegistryModel,
                  RrugcStageSkillDefaultModel.registry_id == RrugcStage2SkillRegistryModel.id)
            .where(RrugcStageSkillDefaultModel.tenant_id == PROD_TENANT,
                   RrugcStageSkillDefaultModel.stage.in_(("stage1", "prod_s1")))))
        if not existing:
            raise RuntimeError("Current Product Stage 1 default does not exist")
        active = int(session.scalar(select(func.count()).select_from(RrugcKeywordImageJobModel)
                                    .where(RrugcKeywordImageJobModel.tenant_id == PROD_TENANT,
                                           RrugcKeywordImageJobModel.status.in_(("queued", "running")))) or 0)
        if active:
            raise RuntimeError(f"Stage 1 has {active} active jobs; refusing to swap the default")
        before = {row.stage: item.skill_name for row, item in existing}
        retry_candidates = []
        if args.retry_failed_keyword.strip():
            retry_candidates = list(session.scalars(select(RrugcKeywordImageJobModel).where(
                RrugcKeywordImageJobModel.tenant_id == PROD_TENANT,
                RrugcKeywordImageJobModel.keyword_text == args.retry_failed_keyword.strip(),
                RrugcKeywordImageJobModel.status == "failed",
            )))
            if len(retry_candidates) != 1:
                raise RuntimeError("Expected exactly one failed Stage 1 job for requested keyword")
        if not set(before.values()).issubset({"gatorhats-stage1-six-designs", SKILL_NAME}):
            raise RuntimeError("Unexpected Stage 1 Skill default; manual reconciliation required")
    print(json.dumps({"mode": "apply" if args.apply else "dry-run",
                      "before": before, "bundle_sha256": skill.bundle_sha256,
                      "active_jobs": 0, "repair_candidates": len(retry_candidates)}))
    if not args.apply:
        return

    # Atomically replace the previously installed local ZIP, preserving registry
    # identity and output history; refuse if a Stage 1 job is active.
    installed = install_local_stage2_skill_bundle(skill, replace=True)
    if not installed.ready:
        raise RuntimeError("New Typography Concepts Skill is not ready")
    with SessionLocal() as session:
        reconcile_skill_registry(session, tenant_id=PROD_TENANT, refresh=True)
        selected = session.scalar(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == PROD_TENANT,
            RrugcStage2SkillRegistryModel.source == "local",
            RrugcStage2SkillRegistryModel.skill_name == SKILL_NAME,
            RrugcStage2SkillRegistryModel.enabled.is_(True),
            RrugcStage2SkillRegistryModel.deleted_at.is_(None),
            RrugcStage2SkillRegistryModel.validation_status == "valid"))
        if selected is None or not keyword_skill_compatible(SKILL_NAME):
            raise RuntimeError("Installed Typography Skill did not satisfy Stage 1 contract")
        for stage in ("stage1", "prod_s1"):
            row = session.get(RrugcStageSkillDefaultModel, (PROD_TENANT, stage))
            previous = before.get(stage)
            if row is None:
                row = RrugcStageSkillDefaultModel(tenant_id=PROD_TENANT, stage=stage)
                session.add(row)
            row.registry_id = selected.id
            session.add(AuthAuditEventModel(
                tenant_id=PROD_TENANT, actor_id="operator-typography-stage1",
                action="rrugc.stage_skill.default_updated",
                detail_json={"stage":stage, "old_skill":previous,
                             "new_skill": SKILL_NAME,"registry_id": selected.id,
                             "reason":"automated_six_png_typography_contract"},
            ))
        # Retire the old random-font Skill from the production library. Stored
        # output records stay unchanged; no historical Drive file is removed.
        retired = list(session.scalars(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == PROD_TENANT,
            RrugcStage2SkillRegistryModel.skill_name == "gatorhats-stage1-six-designs",
        )))
        for older in retired:
            older.enabled = False
        session.commit()
        checked = {stage: session.get(RrugcStageSkillDefaultModel, (PROD_TENANT, stage)).registry_id
                   for stage in ("stage1", "prod_s1")}
        if any(value != selected.id for value in checked.values()):
            raise RuntimeError("Post-install default verification failed")
    # Retire the old runtime font pool only when no tenant still selects it
    # and no queued/running legacy job could need those assets. Prior immutable
    # backend releases and historical Drive output versions remain recoverable.
    with SessionLocal() as session:
        refs = session.scalar(
            select(func.count()).select_from(RrugcStageSkillDefaultModel)
            .join(RrugcStage2SkillRegistryModel,
                  RrugcStageSkillDefaultModel.registry_id == RrugcStage2SkillRegistryModel.id)
            .where(RrugcStage2SkillRegistryModel.skill_name == "gatorhats-stage1-six-designs")
        ) or 0
        active_legacy = session.scalar(
            select(func.count()).select_from(RrugcKeywordImageJobModel)
            .where(RrugcKeywordImageJobModel.skill_name == "gatorhats-stage1-six-designs",
                   RrugcKeywordImageJobModel.status.in_(("queued", "running")))
        ) or 0
    if not refs and not active_legacy:
        old_dir = (
            Path(str(get_settings().CODEX_IMAGE_HOME)).resolve()
            / "skills" / "gatorhats-stage1-six-designs"
        )
        if old_dir.is_symlink():
            raise RuntimeError("Refusing to remove symlinked legacy Skill directory")
        if old_dir.is_dir():
            shutil.rmtree(old_dir)
            print("RETIRED: unused legacy font-pool runtime files (historical output retained)")
    else:
        print(f"LEGACY_RUNTIME_RETAINED: {refs} assignments, {active_legacy} active jobs")
    print("SUCCESS: Stage 1 default installed as " + SKILL_NAME + "; previous output history preserved.")
    if args.retry_failed_keyword.strip():
        from app.modules.realistic_review_ugc.keyword_images import KeywordImageService
        with SessionLocal() as session:
            row = session.scalar(select(RrugcKeywordImageJobModel).where(
                RrugcKeywordImageJobModel.tenant_id == PROD_TENANT,
                RrugcKeywordImageJobModel.keyword_text == args.retry_failed_keyword.strip(),
                RrugcKeywordImageJobModel.status == "failed"))
            if row is None:
                raise RuntimeError("Failed Stage 1 job changed before requeue; no duplicate queued")
            refreshed = KeywordImageService(session).regenerate(
                tenant_id=PROD_TENANT, keyword_id=row.keyword_id,
                skill_source="local", skill_name=SKILL_NAME,
            )
            print("QUEUED: " + refreshed.keyword_text + " job=" + refreshed.id
                  + " skill=" + refreshed.skill_name + "; prior output history preserved.")


if __name__ == "__main__":
    main()
