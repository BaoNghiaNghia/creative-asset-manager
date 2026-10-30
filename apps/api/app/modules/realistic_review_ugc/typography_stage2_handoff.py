"""MVP: explicitly selected, persisted Typography V2 PNG -> Products Stage 2 source.

No ImageGen calls, V1 updates, Scout auto-start or Stage 2 jobs. A separate
default-OFF handoff gate ensures deployment alone never publishes V2 outputs.
Callers must enforce authenticated read + generate permissions.
"""
from __future__ import annotations

from typing import Any
from pathlib import PurePosixPath
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcImageOutputVersionModel, RrugcKeywordVolumeModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.typography_output_history import (
    HISTORY_STAGE, HISTORY_SCHEMA, PNG_MIME,
)
from app.modules.realistic_review_ugc.typography_imagegen_pilot import (
    TypographyPilotError, fingerprint, resolve_plan, enabled as pilot_enabled,
)
from app.modules.auth_persistence.model import AuthAuditEventModel


def handoff_enabled(settings: Any, tenant_id: str) -> bool:
    return bool(pilot_enabled(settings, tenant_id)
                and getattr(settings, "TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED", False) is True
                and getattr(settings, "TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED", False) is True
                and getattr(settings, "MANAGED_ASSET_STORAGE_ENABLED", False) is True)


def select_saved_v2_design(
    session: Session, *, settings: Any, tenant_id: str, user_id: str,
    keyword_id: str, version: int, selected: bool,
) -> dict:
    """Only saved immutable and still-authorized V2 output can become a source.

    Use the picked keyword row lock to serialize selection. Existing V1 plans
    and their selection remain untouched. Caller owns commit/rollback.
    """
    if not handoff_enabled(settings, tenant_id):
        raise TypographyPilotError("handoff_disabled", "V2 handoff is disabled.")
    if type(version) is not int or version < 1 or type(selected) is not bool:
        raise TypographyPilotError("handoff_input_invalid", "Invalid saved output selection.")
    if not isinstance(user_id, str) or not user_id.strip():
        raise TypographyPilotError("operator_invalid", "Authenticated operator required.")

    keyword = session.scalar(select(RrugcKeywordVolumeModel).where(
        RrugcKeywordVolumeModel.tenant_id == tenant_id,
        RrugcKeywordVolumeModel.id == keyword_id,
    ).with_for_update())
    if keyword is None or keyword.picked is not True:
        raise TypographyPilotError("keyword_not_picked", "Picked keyword not found.")

    row = session.scalar(select(RrugcImageOutputVersionModel).where(
        RrugcImageOutputVersionModel.tenant_id == tenant_id,
        RrugcImageOutputVersionModel.stage == HISTORY_STAGE,
        RrugcImageOutputVersionModel.job_id == keyword_id,
        RrugcImageOutputVersionModel.version == version,
    ))
    if row is None:
        raise TypographyPilotError("history_missing", "Saved V2 version was not found.")
    info = row.font_info_json
    if not isinstance(info, dict) or any((
        info.get("schema_version") != HISTORY_SCHEMA,
        info.get("slot") not in range(1, 7),
        not isinstance(info.get("png_sha256"), str),
        len(str(info.get("png_sha256"))) != 64,
        not isinstance(info.get("reference_id"), str),
        not isinstance(info.get("plan_sha256"), str),
        len(str(info.get("plan_sha256"))) != 64,
        row.content_type != PNG_MIME,
        row.width != 2048, row.height != 1024,
        not isinstance(row.remote_file_id, str) or not row.remote_file_id,
        row.size_bytes is None or row.size_bytes <= 0,
        row.processing_job_id is None,
    )):
        raise TypographyPilotError("history_invalid", "Saved PNG provenance invalid.")
    # Do not trust the historical human review once rights/accepted DNA change.
    current = resolve_plan(session, tenant_id=tenant_id, user_id=user_id,
                           keyword_id=keyword_id, reference_id=info["reference_id"])
    if fingerprint(current) != info["plan_sha256"] or current.get("source_keyword_exact") != keyword.keyword:
        raise TypographyPilotError("source_changed", "Review source rights or keyword changed.")

    # An existing source with the same managed ID could belong to V1; never
    # mutate it. The synthetic path/revision identifies V2 ownership.
    plan = session.scalar(select(RrugcSourcePlanModel).where(
        RrugcSourcePlanModel.tenant_id == tenant_id,
        RrugcSourcePlanModel.root_folder_id == PRODUCTS_STAGE1_SELECTED_ROOT,
        RrugcSourcePlanModel.source_file_id == row.remote_file_id,
    ).with_for_update())
    expected_path = f"Typography V2/{keyword_id}/{row.id}/design_{info['slot']:02d}.png"
    if plan is not None and (plan.source_revision != row.id
                             or plan.source_relative_path != expected_path):
        raise TypographyPilotError("handoff_conflict", "Existing source belongs to another history record.")

    if selected:
        if plan is None:
            plan = RrugcSourcePlanModel(
                tenant_id=tenant_id,
                root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
                source_file_id=row.remote_file_id,
                source_parent_folder_id=None,
                source_relative_path=expected_path,
                source_name=PurePosixPath(expected_path).name,
                source_mime_type=PNG_MIME,
                source_size_bytes=row.size_bytes,
                source_width=row.width,
                source_height=row.height,
                source_revision=row.id,
                status="ready",
                created_by_user_id=user_id,
            )
            session.add(plan)
        else:
            plan.status = "ready"
    elif plan is not None:
        plan.status = "missing"

    session.flush()
    session.add(AuthAuditEventModel(
        tenant_id=tenant_id, actor_id=user_id,
        action="typography_v2.stage2_selection",
        detail_json={"keyword_id":keyword_id,"version":version,
                     "selected":selected,"source_plan_id":plan.id if plan is not None else None},
    ))
    return {
        "selected": selected and plan is not None,
        "source_plan_id": plan.id if plan is not None else None,
        "stage2_jobs_created": 0,
        "scout_started": False,
        "v1_modified": False,
    }
