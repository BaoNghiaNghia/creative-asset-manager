"""Persist the randomly selected Stage 2 hat input.

Revision ID: 0128_rrugc_stage2_random_hat_input
Revises: 0127_rrugc_stage2_skill_crud
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0128_rrugc_stage2_random_hat_input"
down_revision = "0127_rrugc_stage2_skill_crud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_stage2_jobs",
        sa.Column("selected_source_snapshot_json", sa.JSON()),
    )


def downgrade() -> None:
    op.drop_column("rrugc_stage2_jobs", "selected_source_snapshot_json")
