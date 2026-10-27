"""Assign the project-managed true-thread v2 Skill only to Products Stage 2 Slot 1.

Dry-run by default. --apply updates future default only, not existing pinned,
queued, running or completed Ecommerce jobs. --rollback restores original v1.
Run after a verified backend release synced project-managed Codex Skills.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.auth_persistence.model import AuthAuditEventModel
from app.modules.realistic_review_ugc.model import (
    RrugcStage2SkillRegistryModel, RrugcStageSkillDefaultModel,
)
from app.modules.realistic_review_ugc.skill_registry import ensure_skill_registry
from app.modules.realistic_review_ugc.stage2_skills import installed_stage2_skill_sha256
from app.providers.ai.codex_image import load_codex_skill_manifest

TENANT = "creative-assets-prod"
ACTOR = "operator-slot1-thread-fidelity-v2"
STAGE = "prod_s2_ec1"
TARGET = "gatorhats-8869-ecommerce-front-thread-v2"
ROLLBACK = "8869-ecom-front-fine-thread-v1"
ALLOWED_PREVIOUS = frozenset({
    ROLLBACK, "8869-ecom-front", "gatorhats-8869-ecommerce-front", TARGET,
})


def _get_stage(session):
    row = session.get(RrugcStageSkillDefaultModel, (TENANT, STAGE))
    if row is None or not row.registry_id:
        raise RuntimeError("Slot 1 does not have an installed Skill assignment")
    registered = session.get(RrugcStage2SkillRegistryModel, row.registry_id)
    if registered is None:
        raise RuntimeError("The currently assigned Slot 1 Skill is missing")
    return row, registered


def _get_target(session, name: str):
    registered = session.scalar(select(RrugcStage2SkillRegistryModel).where(
        RrugcStage2SkillRegistryModel.tenant_id == TENANT,
        RrugcStage2SkillRegistryModel.skill_name == name,
        RrugcStage2SkillRegistryModel.source == "local",
        RrugcStage2SkillRegistryModel.enabled.is_(True),
        RrugcStage2SkillRegistryModel.validation_status == "valid",
        RrugcStage2SkillRegistryModel.deleted_at.is_(None),
    ))
    if registered is None:
        raise RuntimeError("Target Slot 1 Skill was not installed / registered: " + name)
    digest = installed_stage2_skill_sha256(name)
    if not digest:
        raise RuntimeError("Target Slot 1 Skill is absent from Codex runtime")
    manifest = load_codex_skill_manifest(get_settings().CODEX_IMAGE_HOME, name)
    if manifest is None or "image_studio" not in manifest.workflows:
        raise RuntimeError("Target Slot 1 Skill is not an image_studio Skill")
    return registered, digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    flags = parser.parse_args()
    desired = ROLLBACK if flags.rollback else TARGET
    with SessionLocal() as session:
        if flags.apply:
            # New project-managed Skill is discovered without changing any
            # preexisting defaults. No network-hosted skill synchronization.
            ensure_skill_registry(session, tenant_id=TENANT)
        row, previous = _get_stage(session)
        if previous.skill_name not in ALLOWED_PREVIOUS:
            raise RuntimeError("Unexpected Slot 1 assignment: " + previous.skill_name)
        target, digest = _get_target(session, desired)
        plan = {"stage": STAGE, "tenant": TENANT,
                "previous": previous.skill_name, "target": desired,
                "target_sha256": digest, "mode": "rollback" if flags.rollback else
                "apply" if flags.apply else "dry-run",
                "other_slots_untouched": True, "previous_versions_preserved": True}
        print(json.dumps(plan))
        if not (flags.apply or flags.rollback) or row.registry_id == target.id:
            return
        row = session.scalar(select(RrugcStageSkillDefaultModel).where(
            RrugcStageSkillDefaultModel.tenant_id == TENANT,
            RrugcStageSkillDefaultModel.stage == STAGE,
        ).with_for_update())
        if row is None or row.registry_id != previous.id:
            raise RuntimeError("Slot 1 assignment changed during activation")
        row.registry_id = target.id
        row.updated_at = datetime.now(timezone.utc)
        row.updated_by_user_id = ACTOR
        session.add(AuthAuditEventModel(
            tenant_id=TENANT, actor_id=ACTOR,
            action="rrugc.stage_skill.default_updated",
            detail_json={"stage": STAGE, "previous_skill": previous.skill_name,
                         "skill_name": desired, "reason": "photographic_thread_v2"},
        ))
        session.commit()
        print(json.dumps({"status": "activated", "stage": STAGE,
                          "skill_name": desired, "old_outputs_preserved": True}))


if __name__ == "__main__":
    main()
