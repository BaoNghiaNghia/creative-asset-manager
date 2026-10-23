"""Preflight for GatorHats Products Stage 2 photo pipeline.

Inspect the actual tenant-scoped Skill assignments before checking files.
The legacy exact-ZIP names are only fallbacks for unassigned slots. Readiness
never enables Multi-hat/UGC generation: those slots still require their
dedicated three-output workers, durable storage, QA and retry contracts.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session
from app.core.config import Settings, get_settings
from app.modules.realistic_review_ugc.model import (
    RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)
from app.providers.ai.codex_image import (
    CodexImageProviderError, load_codex_skill_manifest,
)

ASSIGNED_PHOTO_SLOTS = {"multihat": "prod_s2_mh", "ugc": "prod_s2_ugc"}

PIPELINE_STEPS = (
    {
        "key": "ecommerce",
        "skill_name": "gatorhats-8869-ecommerce-v2-4",
        "title": "Ecommerce",
        "output_count": 4,
        "shot_labels": ["01 · Front High", "02 · Front Right 3/4", "03 · True Right Side", "04 · Back High"],
        "required_assets": ("SKILL.md", "references/SHOT_VISIBILITY_AND_QA.md", "scripts/release_check.py"),
        "required_inputs": "Exact embroidery artwork, verified 8869 stock and four angle reference photos",
    },
    {
        "key": "multihat",
        "skill_name": "gatorhats-8869-multihat-v2-4",
        "title": "Multi-hat",
        "output_count": 3,
        "shot_labels": ["05 · Multi-hat scene", "06 · Multi-hat scene", "07 · Multi-hat scene"],
        "required_assets": ("SKILL.md", "references/STOCK_AND_QA.md", "references/THREAD_MAP.md", "scripts/preflight_stock.py"),
        "required_inputs": "Exact embroidery, verified real stock ZIP and three scene references",
    },
    {
        "key": "ugc",
        "skill_name": "gatorhats-8869-ugc-v3",
        "title": "UGC",
        "output_count": 3,
        "shot_labels": ["08 · UGC scene", "09 · UGC scene", "10 · UGC scene"],
        "required_assets": (
            "SKILL.md", "references/PRODUCT_LOCK.md",
            "references/V3_FIVE_PANEL_AND_NAMES.md", "references/name_pools.json",
            "assets/valucap_8869_stock_images.zip", "assets/FAIL_example_front_split_seam.png",
            "scripts/qa_visual_gate.py",
        ),
        "required_inputs": "Current scene/model references, verified stock and visual five-panel QA",
    },
)


def readiness(
    settings: Settings | None = None,
    *,
    session: Session | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Report the selected tenant Skill, not obsolete hard-coded package names.

    An enabled registry row and a valid on-disk image_studio manifest confirm
    installation. They do NOT imply the three-photo worker is available.
    Without an explicit tenant/session the legacy preflight remains usable.
    """
    config = settings or get_settings()
    root = Path(str(config.CODEX_IMAGE_HOME)).resolve() / "skills"
    result = []
    for spec in PIPELINE_STEPS:
        skill_name = spec["skill_name"]
        selected = None
        if session is not None and tenant_id and spec["key"] in ASSIGNED_PHOTO_SLOTS:
            stage = ASSIGNED_PHOTO_SLOTS[spec["key"]]
            assignment = session.get(
                RrugcStageSkillDefaultModel, (tenant_id, stage)
            )
            if assignment and assignment.registry_id:
                row = session.get(
                    RrugcStage2SkillRegistryModel, assignment.registry_id
                )
                if row and row.tenant_id == tenant_id:
                    selected = row
                    skill_name = row.skill_name or skill_name
            elif assignment is not None:
                # An explicit unassigned slot must not fall back to an old ZIP.
                skill_name = ""
        if selected is not None:
            # The shared library validates an uploaded ZIP on installation.
            # Check that the selected version remains available to the worker.
            skill_dir = root / skill_name
            manifest = None
            if (
                selected.enabled and selected.deleted_at is None
                and selected.validation_status == "valid"
                and selected.workflow == "image_studio"
                and skill_name and skill_dir.is_dir()
                and (skill_dir / "SKILL.md").is_file()
            ):
                try:
                    manifest = load_codex_skill_manifest(
                        str(config.CODEX_IMAGE_HOME), skill_name
                    )
                except (CodexImageProviderError, ValueError, OSError):
                    manifest = None
            installed = bool(
                manifest and "image_studio" in manifest.workflows
                and manifest.skill_name == skill_name
            )
            missing = [] if installed else ["Assigned Skill unavailable or invalid in runtime"]
        elif session is not None and tenant_id and spec["key"] in ASSIGNED_PHOTO_SLOTS:
            # With no usable assignment, do not inspect an unrelated legacy
            # bundle and suggest that it is the user's chosen Skill.
            installed = False
            missing = ["Select an installed image Skill in Manage Skills"]
        else:
            skill = root / skill_name
            missing = [rel for rel in spec["required_assets"] if not (skill / rel).is_file()]
            installed = not missing
        result.append({
            "key": spec["key"],
            "skill_name": skill_name,
            "title": spec["title"],
            "output_count": spec["output_count"],
            "shot_labels": spec["shot_labels"],
            "ready": False,  # No three-photo worker has been enabled.
            "bundle_installed": installed,
            "status": "needs_pipeline_worker" if installed else "needs_install",
            "missing_assets": missing,
            "required_inputs": spec["required_inputs"],
        })
    return {
        "workflow": "products_stage2_photo_pipeline",
        "sequential": True,
        "ready": False,
        "total_outputs_per_design": sum(spec["output_count"] for spec in PIPELINE_STEPS),
        "steps": result,
    }
