"""Add RRUGC Supervisor QA results and correction provenance.

Revision ID: 0098_rrugc_supervisor_qa
Revises: 0097_rrugc_generation_execution
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0098_rrugc_supervisor_qa"
down_revision = "0097_rrugc_generation_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("parent_attempt_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("correction_supervisor_result_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("supervisor_correction_json", sa.JSON()),
    )

    op.create_table(
        "rrugc_supervisor_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("generation_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("supervisor_skill_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=100)),
        sa.Column("metrics_json", sa.JSON()),
        sa.Column("expected_json", sa.JSON()),
        sa.Column("correction_json", sa.JSON()),
        sa.Column("summary", sa.Text()),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("provider_model", sa.String(length=128)),
        sa.Column("processing_job_id", sa.String(length=36)),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_attempt_id"],
            ["rrugc_generation_attempts.id"],
            name="fk_rrugc_supervisor_generation_attempt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_attempt_id",
            "supervisor_skill_version",
            name="uq_rrugc_supervisor_attempt_skill",
        ),
    )
    op.create_index(
        "ix_rrugc_supervisor_campaign_status",
        "rrugc_supervisor_results",
        ["tenant_id", "campaign_id", "status", "created_at"],
    )
    op.create_index(
        "ix_rrugc_supervisor_attempt",
        "rrugc_supervisor_results",
        ["tenant_id", "generation_attempt_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_supervisor_attempt",
        table_name="rrugc_supervisor_results",
    )
    op.drop_index(
        "ix_rrugc_supervisor_campaign_status",
        table_name="rrugc_supervisor_results",
    )
    op.drop_table("rrugc_supervisor_results")
    op.drop_column("rrugc_generation_attempts", "supervisor_correction_json")
    op.drop_column("rrugc_generation_attempts", "correction_supervisor_result_id")
    op.drop_column("rrugc_generation_attempts", "parent_attempt_id")
