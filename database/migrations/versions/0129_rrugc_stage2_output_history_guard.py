"""Protect RRUGC Stage 2 output history from source-plan cleanup.

Revision ID: 0129_rrugc_stage2_output_history_guard
Revises: 0128_rrugc_stage2_random_hat_input
"""
from __future__ import annotations

from alembic import op


revision = "0129_rrugc_stage2_output_history_guard"
down_revision = "0128_rrugc_stage2_random_hat_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_rrugc_stage2_job_source_plan",
        "rrugc_stage2_jobs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_rrugc_stage2_job_source_plan",
        "rrugc_stage2_jobs",
        "rrugc_source_plans",
        ["source_plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_rrugc_stage2_job_source_plan",
        "rrugc_stage2_jobs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_rrugc_stage2_job_source_plan",
        "rrugc_stage2_jobs",
        "rrugc_source_plans",
        ["source_plan_id"],
        ["id"],
        ondelete="CASCADE",
    )
