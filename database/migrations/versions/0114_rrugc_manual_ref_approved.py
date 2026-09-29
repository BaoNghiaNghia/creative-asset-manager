"""Promote manually selected RRUGC references to Approved.

Revision ID: 0114_rrugc_manual_ref_approved
Revises: 0113_rrugc_requeue_good_failed_refs
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0114_rrugc_manual_ref_approved"
down_revision = "0113_rrugc_requeue_good_failed_refs"
branch_labels = None
depends_on = None


_PROMOTABLE_STATUSES = (
    "analysis_failed",
    "analysis_queued",
    "analyzing",
    "needs_review",
    "rejected_no_person",
    "rejected_head_ratio",
    "rejected_expression",
    "rejected_existing_headwear",
    "rejected_head_occlusion",
    "rejected_quality",
    "rejected_context",
)


def upgrade() -> None:
    bind = op.get_bind()
    candidates = sa.table(
        "rrugc_candidates",
        sa.column("id", sa.String(length=36)),
        sa.column("status", sa.String(length=32)),
        sa.column("analyzed_at", sa.DateTime(timezone=True)),
        sa.column("ai_signal_json", sa.JSON()),
        sa.column("ai_manual_label", sa.String(length=16)),
        sa.column("last_error_code", sa.String(length=100)),
        sa.column("reject_reason", sa.String(length=100)),
    )

    rows = bind.execute(
        sa.select(
            candidates.c.id,
            candidates.c.status,
            candidates.c.analyzed_at,
            candidates.c.ai_signal_json,
            candidates.c.ai_manual_label,
            candidates.c.reject_reason,
        ).where(candidates.c.status.in_(_PROMOTABLE_STATUSES))
    )

    promoted = 0
    for row in rows.mappings():
        signal = row["ai_signal_json"]
        if not isinstance(signal, dict):
            continue
        if signal.get("reference_manual_label") != "good":
            continue
        if row["ai_manual_label"] == "ai":
            continue

        signal = dict(signal)
        signal["reference_manual_approval_override"] = True
        signal["reference_manual_auto_status"] = row["status"]
        signal["reference_manual_auto_reject_reason"] = row["reject_reason"]
        signal["reference_manual_pending_analysis"] = row["analyzed_at"] is None
        signal.pop("reference_manual_pending_approval", None)

        bind.execute(
            sa.update(candidates)
            .where(candidates.c.id == row["id"])
            .values(
                status="approved",
                ai_signal_json=signal,
                last_error_code=None,
                reject_reason=None,
            )
        )
        promoted += 1

    print(f"RRUGC recovery: promoted {promoted} manual REF-good candidate(s) to approved")


def downgrade() -> None:
    # Manual reference qualification is user intent. Reverting promoted rows
    # after background analysis/import may have advanced would be unsafe.
    pass
