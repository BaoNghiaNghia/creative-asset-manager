"""Add RRUGC review-task handoff and final review provenance.

Revision ID: 0099_rrugc_review_handoff
Revises: 0098_rrugc_supervisor_qa
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0099_rrugc_review_handoff"
down_revision = "0098_rrugc_supervisor_qa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("review_status", sa.String(length=32)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("review_task_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("reviewed_by_user_id", sa.String(length=255)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("review_note", sa.Text()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("export_status", sa.String(length=32)),
    )

    op.create_table(
        "rrugc_review_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("generation_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("supervisor_result_id", sa.String(length=36), nullable=False),
        sa.Column("queue_reason", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("review_note", sa.Text()),
        sa.Column("reviewed_by_user_id", sa.String(length=255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_attempt_id"],
            ["rrugc_generation_attempts.id"],
            name="fk_rrugc_review_task_generation_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supervisor_result_id"],
            ["rrugc_supervisor_results.id"],
            name="fk_rrugc_review_task_supervisor_result",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_attempt_id",
            name="uq_rrugc_review_task_attempt",
        ),
    )
    op.create_index(
        "ix_rrugc_review_task_status_priority",
        "rrugc_review_tasks",
        ["tenant_id", "status", "priority", "created_at"],
    )
    op.create_index(
        "ix_rrugc_review_task_campaign",
        "rrugc_review_tasks",
        ["tenant_id", "campaign_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_review_task_campaign",
        table_name="rrugc_review_tasks",
    )
    op.drop_index(
        "ix_rrugc_review_task_status_priority",
        table_name="rrugc_review_tasks",
    )
    op.drop_table("rrugc_review_tasks")
    op.drop_column("rrugc_generation_attempts", "export_status")
    op.drop_column("rrugc_generation_attempts", "review_note")
    op.drop_column("rrugc_generation_attempts", "reviewed_at")
    op.drop_column("rrugc_generation_attempts", "reviewed_by_user_id")
    op.drop_column("rrugc_generation_attempts", "review_task_id")
    op.drop_column("rrugc_generation_attempts", "review_status")
