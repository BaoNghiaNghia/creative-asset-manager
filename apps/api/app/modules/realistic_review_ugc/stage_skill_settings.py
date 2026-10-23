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

# Stage names fit the existing String(16) database column; no data migration.
# Keep legacy stage1/stage2/stage4 for old clients, but never write a shared
# stage key from the new workflow-specific Manage Skills dialogs.
# Keep previous 7-slot assignments readable without reinterpreting old
# Ecommerce/Multi-hat/UGC/Colorway keys as single-photo outputs.
LEGACY_PRODUCT_STAGE2_SLOTS = tuple(f"prod_s2_{i}" for i in range(1, 8))
LEGACY_STAGES = ("stage1", "stage2", "stage4", *LEGACY_PRODUCT_STAGE2_SLOTS)
ECOMMERCE_SHOT_SLOTS = tuple(f"prod_s2_ec{i}" for i in range(1, 5))
PRODUCT_STAGE2_SLOTS = (*ECOMMERCE_SHOT_SLOTS, "prod_s2_mh", "prod_s2_ugc", "prod_s2_col")
PRODUCT_STAGE2_PHOTO_SKILLS = {
    # Backwards-compatible read/write rules for previously deployed clients.
    "prod_s2_1": "gatorhats-8869-ecommerce-v2-4",
    "prod_s2_2": "gatorhats-8869-multihat-v2-4",
    "prod_s2_3": "gatorhats-8869-ugc-v3",
    # Current group-specific slots.
    "prod_s2_mh": "gatorhats-8869-multihat-v2-4",
    "prod_s2_ugc": "gatorhats-8869-ugc-v3",
}
WORKFLOW_STAGES = ("prod_s1", *PRODUCT_STAGE2_SLOTS, "rev_s2", "rev_s4")
STAGES = (*LEGACY_STAGES, *WORKFLOW_STAGES)
# Inherited legacy groups are read-only fallbacks. Crucially, old
# prod_s2_1 was a FOUR-output bundle, not a single Ecommerce image.
LEGACY_FALLBACKS = {
    "prod_s1": "stage1",
    "prod_s2_4": "stage2",
    "prod_s2_mh": "prod_s2_2",
    "prod_s2_ugc": "prod_s2_3",
    "prod_s2_col": "prod_s2_4",
    "rev_s2": "stage2",
    "rev_s4": "stage4",
}


def keyword_skill_compatible(skill_name: str, *, settings: Settings | None = None) -> bool:
    """Accept any installed image-generation Skill; no special Stage 1 label is required.

    Stage 1 sends keyword text without source images. A Skill may use bundled
    assets or generate from text even when its manifest lists reference roles.
    """
    settings = settings or get_settings()
    try:
        manifest = load_codex_skill_manifest(settings.CODEX_IMAGE_HOME, skill_name)
    except CodexImageProviderError:
        return False
    # Stage 1 has a fixed six-file contract. Legacy image_studio skills can
    # generate 10+ concepts and 13 colorways; do not advertise them as ready.
    return bool(manifest is not None and "keyword_six_designs" in manifest.workflows)


def stage_skill_defaults(session: Session, *, tenant_id: str) -> dict[str, str]:
    """Tenant-scoped selections with independent Product and Review defaults.

    Explicit empty assignments suppress migration fallbacks. Deleted or
    invalid skills are never returned as a selectable default.
    """
    rows = session.execute(
        select(RrugcStageSkillDefaultModel, RrugcStage2SkillRegistryModel)
        .outerjoin(
            RrugcStage2SkillRegistryModel,
            RrugcStage2SkillRegistryModel.id == RrugcStageSkillDefaultModel.registry_id,
        )
        .where(
            RrugcStageSkillDefaultModel.tenant_id == tenant_id,
            RrugcStageSkillDefaultModel.stage.in_(STAGES),
        )
    ).all()
    explicit = {row.stage for row, _skill in rows}
    selections = {
        row.stage: f"{skill.source}:{skill.skill_id or skill.skill_name}"
        for row, skill in rows
        if skill is not None
        and skill.tenant_id == tenant_id
        and skill.enabled
        and skill.deleted_at is None
        and skill.validation_status == "valid"
        and (row.stage not in ("stage1", "prod_s1")
             or keyword_skill_compatible(skill.skill_name))
    }
    # An explicit null/disabled workflow selection is different from a missing
    # row: return an empty string so browsers never fall back to legacy stages.
    for stage in explicit.intersection((*WORKFLOW_STAGES, *LEGACY_PRODUCT_STAGE2_SLOTS)):
        selections.setdefault(stage, "")
    for stage, legacy in LEGACY_FALLBACKS.items():
        if stage not in explicit and legacy in selections:
            selections[stage] = selections[legacy]
    return selections


def set_stage_skill_default(
    session: Session, *, tenant_id: str, actor_id: str,
    stage: str, registry_id: str | None,
) -> dict[str, str]:
    if stage not in STAGES:
        raise Stage2SkillRegistryError(
            "stage_skill_invalid_stage",
            "Only configured Product/Review image-generation stages support Skill defaults.",
            status_code=422,
        )
    selected = None
    if registry_id:
        selected = get_registry_row(session, tenant_id=tenant_id, registry_id=registry_id)
        if not selected.enabled or selected.validation_status != "valid" or selected.deleted_at is not None:
            raise Stage2SkillRegistryError("stage_skill_unavailable", "Skill must be enabled, installed and valid.", status_code=409)
        catalog = list_stage2_skill_catalog(refresh=False)
        if not any(item.source == selected.source and item.skill_name == selected.skill_name and item.ready
                   for item in catalog.items):
            raise Stage2SkillRegistryError("stage_skill_not_ready", "The selected Skill is not ready in the runtime.", status_code=409)
        if stage in ("stage1", "prod_s1"):
            if not keyword_skill_compatible(selected.skill_name):
                raise Stage2SkillRegistryError(
                    "stage_skill_requires_references",
                    "Product Stage 1 requires an installed six-design keyword Skill.",
                    status_code=422,
                )
        elif stage in PRODUCT_STAGE2_PHOTO_SKILLS:
            # Slot 5/6 are Skill *assignments*, not active three-image workers.
            # Uploads have distinct names (e.g. ugc-gatorhats) and must remain
            # selectable after they pass runtime image_studio validation.
            # Never infer that assigning a generic Skill enables generation.
            manifest = load_codex_skill_manifest(
                get_settings().CODEX_IMAGE_HOME, selected.skill_name
            )
            if manifest is None or "image_studio" not in manifest.workflows:
                raise Stage2SkillRegistryError(
                    "stage_skill_workflow_incompatible",
                    "Multi-hat/UGC assignment requires an installed image_studio Skill.",
                    status_code=422,
                )
        elif stage in ECOMMERCE_SHOT_SLOTS:
            # A bulk four-photo bundle cannot silently run as one photo Skill.
            if selected.skill_name == "gatorhats-8869-ecommerce-v2-4":
                raise Stage2SkillRegistryError(
                    "stage_skill_requires_single_image",
                    "Choose a dedicated Skill for this Ecommerce image, not the four-output bundle.",
                    status_code=422,
                )
            manifest = load_codex_skill_manifest(
                get_settings().CODEX_IMAGE_HOME, selected.skill_name
            )
            if manifest is None or "image_studio" not in manifest.workflows:
                raise Stage2SkillRegistryError(
                    "stage_skill_workflow_incompatible",
                    "An Ecommerce photo Skill must support image_studio.",
                    status_code=422,
                )
        elif stage not in LEGACY_PRODUCT_STAGE2_SLOTS[4:]:
            manifest = load_codex_skill_manifest(
                get_settings().CODEX_IMAGE_HOME, selected.skill_name
            )
            if manifest is None or "image_studio" not in manifest.workflows:
                raise Stage2SkillRegistryError(
                    "stage_skill_workflow_incompatible",
                    "Colorway and Review stages require an installed image_studio Skill.",
                    status_code=422,
                )
        if stage in PRODUCT_STAGE2_SLOTS:
            # Seven distinct assignments; a configured row may not reuse
            # another slot's Skill (including inherited legacy colorway default).
            selected_key = f"{selected.source}:{selected.skill_id or selected.skill_name}"
            effective = stage_skill_defaults(session, tenant_id=tenant_id)
            if any(effective.get(other) == selected_key
                   for other in PRODUCT_STAGE2_SLOTS if other != stage):
                raise Stage2SkillRegistryError(
                    "stage_skill_duplicate_assignment",
                    "Each Product Stage 2 slot requires a different Skill.",
                    status_code=409,
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