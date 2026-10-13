"""Resolve the exact hat input used to generate a Stage 4 UGC image.

The job's immutable selected-source snapshot is authoritative. Legacy jobs
may use their source plan only when its revision still matches the job.
Never silently compare an output against a later replaced source image.
"""
from __future__ import annotations

from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel, RrugcStage2JobModel


def stage5_before_source(
    job: RrugcStage2JobModel,
    plan: RrugcSourcePlanModel | None,
) -> tuple[str, str | None, int | None] | None:
    snapshot = job.selected_source_snapshot_json
    if isinstance(snapshot, dict) and snapshot:
        remote_id = str(snapshot.get("remote_file_id") or "").strip()
        if not remote_id:
            return None
        return (
            remote_id,
            str(snapshot["content_type"]) if snapshot.get("content_type") else None,
            snapshot.get("size_bytes"),
        )
    if (
        plan is not None
        and plan.tenant_id == job.tenant_id
        and plan.id == job.source_plan_id
        and plan.source_revision == job.source_revision
        and plan.source_file_id
    ):
        return plan.source_file_id, plan.source_mime_type, plan.source_size_bytes
    return None
