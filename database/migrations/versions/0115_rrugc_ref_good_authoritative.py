"""Make RRUGC human REF-good qualification authoritative.

Revision ID: 0115_rrugc_ref_good_authoritative
Revises: 0114_rrugc_manual_ref_approved
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0115_rrugc_ref_good_authoritative"
down_revision = "0114_rrugc_manual_ref_approved"
branch_labels = None
depends_on = None


_DRIVE_LIFECYCLE_STATUSES = {
    "import_queued",
    "importing",
    "drive_ready",
    "import_failed",
}


def upgrade() -> None:
    bind = op.get_bind()
    candidates = sa.table(
        "rrugc_candidates",
        sa.column("id", sa.String(length=36)),
        sa.column("status", sa.String(length=32)),
        sa.column("analyzed_at", sa.DateTime(timezone=True)),
        sa.column("ai_signal_json", sa.JSON()),
        sa.column("last_error_code", sa.String(length=100)),
        sa.column("reject_reason", sa.String(length=100)),
    )

    rows = bind.execute(
        sa.select(
            candidates.c.id,
            candidates.c.status,
            candidates.c.analyzed_at,
            candidates.c.ai_signal_json,
            candidates.c.reject_reason,
        ).where(candidates.c.status != "approved")
    )

    promoted = 0
    for row in rows.mappings():
        if row["status"] in _DRIVE_LIFECYCLE_STATUSES:
            continue
        signal = row["ai_signal_json"]
        if not isinstance(signal, dict):
            continue
        if signal.get("reference_manual_label") != "good":
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

    print(
        "RRUGC recovery: promoted "
        f"{promoted} authoritative manual REF-good candidate(s) to approved"
    )


def downgrade() -> None:
    # Human reference qualification is durable user intent. Reconstructing a
    # stale automated rejection after the application has used the reference
    # would be unsafe, so downgrade intentionally preserves repaired rows.
    pass
