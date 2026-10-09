"""Tenant-wide skill choices for user-visible image-generation stages 1, 2 and 4."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.realistic_review_ugc.model import RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel
from app.modules.realistic_review_ugc.skill_registry import (
    Stage2SkillRegistryError, _audit, get_registry_row,
)
from app.modules.realistic_review_ugc.stage2_skills import list_stage2_skill_catalog
from app.providers.ai.codex_image import CodexImageProviderError, load_codex_skill_manifest

STAGES = ("stage1", "stage2", "stage4")


def keyword_skill_compatible(skill_name: str, *, settings: Settings | None = None) -> bool:
    """Check actual installed manifest, not just Stage 2 'ready' status."""
    settings = settings or get_settings()
    try:
        manifest = load_codex_skill_manifest(settings.CODEX_IMAGE_HOME, skill_name)
    except CodexImageProviderError:
        return False
    return bool(
        manifest is not None
        and "keyword_artwork" in manifest.workflows
        and not manifest.required_reference_roles
    )


def stage_skill_defaults(session: Session, *, tenant_id: str) -> dict[str, str]:
    """Stable selection keys shared with client; ignore disabled, missing or deleted skills."""
    rows = session.execute(
        select(RrugcStageSkillDefaultModel.stage, RrugcStage2SkillRegistryModel)
        .join(RrugcStage2SkillRegistryModel,
              RrugcStage2SkillRegistryModel.id == RrugcStageSkillDefaultModel.registry_id)
        .where(RrugcStageSkillDefaultModel.tenant_id == tenant_id,
               RrugcStageSkillDefaultModel.stage.in_(STAGES),
               RrugcStage2SkillRegistryModel.tenant_id == tenant_id,
               RrugcStage2SkillRegistryModel.enabled.is_(True),
               RrugcStage2SkillRegistryModel.deleted_at.is_(None),
               RrugcStage2SkillRegistryModel.validation_status == "valid")
    )
    return {
        stage: f"{skill.source}:{skill.skill_id or skill.skill_name}"
        for stage, skill in rows
        if stage != "stage1" or keyword_skill_compatible(skill.skill_name)
    }


def set_stage_skill_default(
    session: Session, *, tenant_id: str, actor_id: str,
    stage: str, registry_id: str | None,
) -> dict[str, str]:
    if stage not in STAGES:
        raise Stage2SkillRegistryError("stage_skill_invalid_stage", "Only stages 1, 2 and 4 support generation defaults.", status_code=422)
    selected = None
    if registry_id:
        selected = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
        if not selected.enabled or selected.validation_status != "valid" or selected.deleted_at is not None:
            raise Stage2SkillRegistryError("stage_skill_unavailable", "Skill must be enabled, installed and valid.", status_code=409)
        catalog = list_stage2_skill_catalog(refresh=False)
        if not any(item.source == selected.source and item.skill_name == selected.skill_name and item.ready
                   for item in catalog.items):
            raise Stage2SkillRegistryError("stage_skill_not_ready", "The selected Skill is not ready in the runtime.", status_code=409)
        if stage == "stage1":
            if not keyword_skill_compatible(selected.skill_name):
                raise Stage2SkillRegistryError(
                    "stage_skill_requires_references",
                    "Stage 1 requires a keyword-artwork Skill with no reference images.", status_code=422,
                )
        else:
            manifest = load_codex_skill_manifest(get_settings().CODEX_IMAGE_HOME, selected.skill_name)
            if manifest is None or "image_studio" not in manifest.workflows:
                raise Stage2SkillRegistryError(
                    "stage_skill_workflow_incompatible",
                    "Stages 2 and 4 require an installed image_studio Skill.", status_code=422,
                )
    row = session.get(RrugcStageSkillDefaultModel, (tenant_id, stage))
    if row is None:
        row = RrugcStageSkillDefaultModel(tenant_id=tenant_id, stage=stage)
        session.add(row)
    row.registry_id = selected.id if selected else None
    row.updated_by_user_id = actor_id
    row.updated_at = datetime.now(timezone.utc)
    _audit(session, tenant_id=tenant_id, actor_id=actor_id, action="rrugc.stage_skill.default_updated",
           detail={"stage": stage, "registry_id": row.registry_id,
                   "skill_name": selected.skill_name if selected else None})
    session.commit()
    return stage_skill_defaults(session, tenant_id=tenant_id)
