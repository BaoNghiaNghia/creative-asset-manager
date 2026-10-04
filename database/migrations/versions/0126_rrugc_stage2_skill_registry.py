"""Add versioned skill metadata to RRUGC Stage 2 jobs.

Revision ID: 0126_rrugc_stage2_skill_registry
Revises: 0125_rrugc_stage2_jobs
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0126_rrugc_stage2_skill_registry"
down_revision = "0125_rrugc_stage2_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_stage2_jobs",
        sa.Column("skill_source", sa.String(length=32), nullable=False, server_default="local"),
    )
    op.add_column(
        "rrugc_stage2_jobs",
        sa.Column("skill_id", sa.String(length=255)),
    )
    op.add_column(
        "rrugc_stage2_jobs",
        sa.Column("skill_version", sa.String(length=64)),
    )
    op.alter_column("rrugc_stage2_jobs", "skill_source", server_default=None)


def downgrade() -> None:
    op.drop_column("rrugc_stage2_jobs", "skill_version")
    op.drop_column("rrugc_stage2_jobs", "skill_id")
    op.drop_column("rrugc_stage2_jobs", "skill_source")
