"""Preflight for GatorHats Products Stage 2 three-skill photo pipeline.

Fail-closed until the exact user-supplied bundles, their supporting assets, and
per-design references are available. This intentionally cannot start jobs:
a worker may only be enabled after the sequential execution contract, persistent
per-shot output storage, logging and retry are implemented and tested.
"""
from __future__ import annotations

from pathlib import Path
from app.core.config import Settings, get_settings

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


def readiness(settings: Settings | None = None) -> dict:
    """Read skill availability without returning system paths or credentials."""
    config = settings or get_settings()
    root = Path(str(config.CODEX_IMAGE_HOME)).resolve() / "skills"
    result = []
    for spec in PIPELINE_STEPS:
        skill = root / spec["skill_name"]
        missing = [rel for rel in spec["required_assets"] if not (skill / rel).is_file()]
        result.append({
            "key": spec["key"],
            "skill_name": spec["skill_name"],
            "title": spec["title"],
            "output_count": spec["output_count"],
            "shot_labels": spec["shot_labels"],
            "ready": False,
            "bundle_installed": len(missing) == 0,
            "status": "needs_install" if missing else "needs_pipeline_worker",
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
