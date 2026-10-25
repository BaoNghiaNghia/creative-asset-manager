"""Upgrade the four assigned 8869 Ecommerce Skills without mutating pinned jobs.

Dry-run by default; --apply installs four new versioned aliases, preserving every
original asset and runnable script, then atomically updates future-job defaults.
--rollback points the four defaults back at the untouched original Skills.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)
from app.modules.realistic_review_ugc.skill_registry import create_skill
from app.modules.realistic_review_ugc.stage2_skills import (
    export_installed_stage2_skill_bundle,
    inspect_uploaded_stage2_skill_bundle,
    installed_stage2_skill_sha256,
)
from app.providers.ai.codex_image import load_codex_skill_manifest

TENANT = "creative-assets-prod"
ACTOR = "operator-fine-thread-8869"
VERSION = "fine-thread-v1"
MIGRATION_TAG = "CAM_FINE_THREAD_V1"
SLOTS = (
    ("prod_s2_ec1", "8869-ecom-front"),
    ("prod_s2_ec2", "8869-ecom-threequatrer"),  # original uploaded name is intentionally misspelled
    ("prod_s2_ec3", "8869-ecom-side"),
    ("prod_s2_ec4", "8869-ecom-back"),
)


def new_name(original: str) -> str:
    return original + "-fine-thread-v1"


def generation_instruction(stage: str) -> str:
    shared = """### CAM product image generation (only for CAM Stage 2 jobs)

CAM identifies this mode with Role-labeled references: and the required
output/final.png. For this mode follow CAM's $imagegen one-image job contract:
use only the current role-labeled inputs, generate exactly ONE catalogue image
and save output/final.png. Do not execute the standalone three-upload Python
scripts below, which require a different input/output contract.

Evidence order: selected-color official stock fixes the exact Valucap 8869
crown, brim, seams, material, orientation, factory side logos and cap color.
Current artwork fixes exact text, font silhouettes, spacing and thread colors.
A completed embroidered-front reference gives composition/placement only:
do NOT copy any inflated 3D relief or overly dark edges from an earlier mockup.
Never add text/logos on an otherwise unembroidered surface.

PHYSICAL THREAD FINISH: machine embroidery at very low relief, showing fine
parallel directional satin/fill stitches and tiny fibers; lightly varied stitch
spacing with soft, subtle thread sheen. Show cap fabric weave between thread
rows. Threads sit close against the cloth with only a shallow, believable lift;
letter silhouette and text proportions must not gain thickness or padding.
Soft diffuse lighting; clean, thin naturally stitched edges.

NEVER produce puffy/foam embroidery, extruded 3D text, bulky padded strokes,
hard embossed bevels, glossy plastic/rubber/vinyl, broad dark halos, deep drop
shadows or harsh black outlines. When the design preview is overly embossed,
preserve its geometry/placement and reconstruct fine real-thread texture,
NOT the original fake depth. No changing previously generated outputs.
"""
    angles = {
        "prod_s2_ec1": """### Slot 1: exact FRONT
Straight-on front catalogue product angle; apply authentic low-profile thread
inside the calibrated front embroidery area. Reproduce every existing glyph
and stitch thread color without inflated thick letter relief.
""",
        "prod_s2_ec2": """### Slot 2: FRONT-RIGHT THREE-QUARTER
Not frontal or a 90-degree side. Foreshorten front lettering and directional
stitches realistically around the curved front crown. Side photo defines cap
geometry only. Retain small side branding only if supplied in official stock,
never invent words or transfer front embroidery to the side.
""",
        "prod_s2_ec3": """### Slot 3: TRUE RIGHT SIDE 90 DEGREES
Only the tiny sliver of front embroidery physically visible around the front
edge may be shown, with correct occlusion and stitch perspective. Never
mirror or paste the whole front logo onto the side. Preserve only official
factory side logos that already exist in the supplied selected-color stock.
""",
        "prod_s2_ec4": """### Slot 4: TRUE REAR 180 DEGREES
Use the exact selected-color official BACK stock view as the primary source:
crown, adjustable strap, seams and material must remain accurate. Keep the
rear crown unembroidered. No copying front lettering, inventing side/rear
logos, puffy stitches or other branding. Factory marks may remain only if
visible in the official stock photograph.
""",
    }
    return shared + "\n" + angles[stage] + "\n### Legacy standalone script mode (not CAM Stage 2)\n"


def replace_once(src: str, old: str, new: str, filename: str) -> str:
    n = src.count(old)
    if n != 1:
        raise ValueError(f"{filename}: expected 1 match, found {n}")
    return src.replace(old, new)


def flatten_relief(src: str, filename: str) -> str:
    return replace_once(
        replace_once(
            src,
            "lo = np.where(low > 1, 1 + (low - 1) * (1 - matte), 1 + (low - 1) * (1 - 0.35 * matte))",
            "lo = 1 + (low - 1) * (1 - 0.90 * matte)  # suppress broad fake relief, retain microstitch grain",
            filename,
        ),
        "rel2 = np.clip(lo * high, 0, 1 + 0.6 * (1 - matte) + 0.15)",
        "rel2 = np.clip(lo * high, 0.82, 1.15)  # fine shallow thread finish",
        filename,
    )


def upgrade_script(stage: str, name: str, data: bytes) -> bytes:
    src = data.decode("utf-8")
    if stage == "prod_s2_ec1" and name == "scripts/run.py":
        src = flatten_relief(replace_once(src, "MATTE = 0.65", "MATTE = 0.86", name), name)
        src = replace_once(src, "fab = reg * (1 - 0.30 * shadow[..., None])",
                           "fab = reg * (1 - 0.08 * shadow[..., None])", name)
    elif stage == "prod_s2_ec2" and name == "scripts/common.py":
        src = replace_once(src, "MATTE = 0.65", "MATTE = 0.86", name)
    elif stage == "prod_s2_ec2" and name == "scripts/run34.py":
        src = flatten_relief(src, name)
    elif stage == "prod_s2_ec3" and name == "scripts/run90.py":
        src = flatten_relief(replace_once(src, "MATTE = 0.65", "MATTE = 0.86", name), name)
    else:
        return data
    compile(src, name, "exec")
    return src.encode("utf-8")


def build_updated_zip(original: str, stage: str, source_zip: bytes) -> bytes:
    """Clone all original files byte-for-byte except documented text/render changes."""
    alias = new_name(original)
    buffer = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(source_zip), "r") as old, zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
    ) as new:
        for info in old.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if name in seen:
                raise ValueError(f"Duplicated source archive entry: {name}")
            seen.add(name)
            data = old.read(name)
            if name == "SKILL.md":
                src = data.decode("utf-8")
                src, matched = re.subn(
                    r"(?m)^name:\s*" + re.escape(original) + r"\s*$",
                    "name: " + alias, src,
                )
                if matched != 1 or not src.startswith("---\n"):
                    raise ValueError(f"Unexpected SKILL.md structure: {original}")
                end = src.find("\n---", 4)
                if end < 0:
                    raise ValueError(f"Missing YAML frontmatter end: {original}")
                after = end + 4
                data = (
                    src[:after] + "\n\n# CAM Fine Thread v1\n\n"
                    + generation_instruction(stage) + src[after:]
                ).encode("utf-8")
            elif name == "manifest.json":
                manifest = json.loads(data.decode("utf-8"))
                if manifest.get("skill_name") != original or "image_studio" not in manifest.get("workflows", []):
                    raise ValueError(f"Original manifest incompatible: {original}")
                manifest["skill_name"] = alias
                manifest["display_name"] = original + " - Fine Thread v1"
                data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            elif name.startswith("scripts/") and name.endswith(".py"):
                data = upgrade_script(stage, name, data)
            new.writestr(name, data)
        new.writestr("references/CAM_FINE_THREAD_V1.txt", (
            f"{MIGRATION_TAG}\nOriginal Skill: {original}\n"
            "Low-profile thread finishing with legacy 3-image deterministic mode retained.\n"
            "Original version remains installed for its existing pinned jobs.\n"
        ).encode("utf-8"))
    if not {"SKILL.md", "manifest.json"} <= seen:
        raise ValueError(f"Original Skill missing required assets: {original}")
    return buffer.getvalue()


@dataclass(frozen=True)
class Plan:
    stage: str
    original: str
    updated: str
    archive: bytes
    exists: bool


def plan_updates(session) -> list[Plan]:
    plans: list[Plan] = []
    for stage, old in SLOTS:
        assigned = session.get(RrugcStageSkillDefaultModel, (TENANT, stage))
        if assigned is None or not assigned.registry_id:
            raise RuntimeError(f"No configured Skill in {stage}")
        item = session.get(RrugcStage2SkillRegistryModel, assigned.registry_id)
        updated = new_name(old)
        if item is None or item.source != "local" or item.skill_name not in (old, updated):
            raise RuntimeError(f"Unexpected assignment in {stage}")
        source_zip = export_installed_stage2_skill_bundle(old)
        archive = build_updated_zip(old, stage, source_zip)
        parsed = inspect_uploaded_stage2_skill_bundle(archive)
        if parsed.skill_name != updated:
            raise RuntimeError(f"New Skill identity mismatch in {stage}")
        existing = session.scalar(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == TENANT,
            RrugcStage2SkillRegistryModel.skill_name == updated,
            RrugcStage2SkillRegistryModel.deleted_at.is_(None),
        ))
        if existing is not None and (
            existing.source != "local" or not existing.enabled or
            installed_stage2_skill_sha256(updated) is None
        ):
            raise RuntimeError(f"Existing upgraded Skill invalid: {updated}")
        plans.append(Plan(stage, old, updated, archive, existing is not None))
    return plans


def set_all_defaults(session, plans: list[Plan], *, rollback: bool = False) -> None:
    """Change all four assignments in one audited transaction; no job edits."""
    desired = {p.stage: p.original if rollback else p.updated for p in plans}
    selections = session.scalars(select(RrugcStageSkillDefaultModel).where(
        RrugcStageSkillDefaultModel.tenant_id == TENANT,
        RrugcStageSkillDefaultModel.stage.in_(tuple(desired)),
    ).with_for_update()).all()
    if len(selections) != 4:
        raise RuntimeError("Expected four Stage 2 defaults")
    for row in selections:
        original = next(p.original for p in plans if p.stage == row.stage)
        previous = session.get(RrugcStage2SkillRegistryModel, row.registry_id)
        if previous is None or previous.skill_name not in (original, new_name(original)):
            raise RuntimeError(f"Unexpected concurrent assignment to {row.stage}")
    targets = {}
    for stage, name in desired.items():
        item = session.scalar(select(RrugcStage2SkillRegistryModel).where(
            RrugcStage2SkillRegistryModel.tenant_id == TENANT,
            RrugcStage2SkillRegistryModel.skill_name == name,
            RrugcStage2SkillRegistryModel.source == "local",
            RrugcStage2SkillRegistryModel.enabled.is_(True),
            RrugcStage2SkillRegistryModel.validation_status == "valid",
            RrugcStage2SkillRegistryModel.deleted_at.is_(None),
        ))
        if item is None or installed_stage2_skill_sha256(name) is None:
            raise RuntimeError(f"Target Skill unavailable: {name}")
        manifest = load_codex_skill_manifest(get_settings().CODEX_IMAGE_HOME, name)
        if manifest is None or "image_studio" not in manifest.workflows:
            raise RuntimeError(f"Target Skill not image_studio-compatible: {name}")
        targets[stage] = item
    now = datetime.now(timezone.utc)
    for row in selections:
        previous = session.get(RrugcStage2SkillRegistryModel, row.registry_id)
        row.registry_id = targets[row.stage].id
        row.updated_by_user_id = ACTOR
        row.updated_at = now
        session.add(AuthAuditEventModel(
            tenant_id=TENANT, actor_id=ACTOR,
            action="rrugc.stage_skill.default_updated",
            detail_json={"stage": row.stage,
                         "previous_skill": previous.skill_name,
                         "skill_name": targets[row.stage].skill_name,
                         "reason": MIGRATION_TAG if not rollback else "fine_thread_rollback"},
        ))
    session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    options = parser.parse_args()
    if options.rollback:
        plans = [Plan(stage, old, new_name(old), b"", True) for stage, old in SLOTS]
        with SessionLocal() as session:
            set_all_defaults(session, plans, rollback=True)
        print(json.dumps({"status": "rolled-back", "slots": 4, "old_skills_preserved": True}))
        return
    with SessionLocal() as session:
        plans = plan_updates(session)
        active = {p.stage: int(session.scalar(select(func.count()).select_from(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.tenant_id == TENANT,
            RrugcEcommerceJobModel.stage == p.stage,
            RrugcEcommerceJobModel.status.in_(("queued", "running")),
        )) or 0) for p in plans}
    print(json.dumps({
        "mode": "apply" if options.apply else "dry-run",
        "version": VERSION,
        "slots": [
            {"stage": p.stage, "previous": p.original, "new": p.updated,
             "zip_bytes": len(p.archive), "already_installed": p.exists,
             "old_active_jobs_untouched": active[p.stage]} for p in plans
        ],
    }))
    if not options.apply:
        return
    for p in plans:
        if not p.exists:
            with SessionLocal() as session:
                create_skill(session, tenant_id=TENANT, actor_id=ACTOR,
                             bundle_bytes=p.archive, replace_existing=False)
            print(f"INSTALLED {p.stage}: {p.updated}")
    with SessionLocal() as session:
        set_all_defaults(session, plans)
    print(json.dumps({"status": "activated", "slots": 4,
                      "old_skills_preserved": True, "old_jobs_untouched": True}))


if __name__ == "__main__":
    main()
