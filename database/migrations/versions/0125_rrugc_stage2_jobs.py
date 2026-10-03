"""Add RRUGC Stage 2 generation jobs.

Revision ID: 0125_rrugc_stage2_jobs
Revises: 0124_rrugc_embroidery_groups
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0125_rrugc_stage2_jobs"
down_revision = "0124_rrugc_embroidery_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_stage2_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("source_plan_id", sa.String(length=36), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("selected_candidate_ids_json", sa.JSON(), nullable=False),
        sa.Column("selected_reference_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("prompt_text", sa.Text()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("processing_job_id", sa.String(length=36)),
        sa.Column("provider_request_id", sa.String(length=255)),
        sa.Column("output_content_hash", sa.String(length=64)),
        sa.Column("output_content_type", sa.String(length=128)),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("output_remote_file_id", sa.String(length=255)),
        sa.Column("output_remote_folder_id", sa.String(length=255)),
        sa.Column("output_web_url", sa.Text()),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_plan_id"],
            ["rrugc_source_plans.id"],
            name="fk_rrugc_stage2_job_source_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_stage2_job_campaign",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_stage2_job_idempotency",
        ),
    )
    op.create_index(
        "ix_rrugc_stage2_job_source_created",
        "rrugc_stage2_jobs",
        ["tenant_id", "source_plan_id", "created_at"],
    )
    op.create_index(
        "ix_rrugc_stage2_job_status_created",
        "rrugc_stage2_jobs",
        ["tenant_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_stage2_job_status_created",
        table_name="rrugc_stage2_jobs",
    )
    op.drop_index(
        "ix_rrugc_stage2_job_source_created",
        table_name="rrugc_stage2_jobs",
    )
    op.drop_table("rrugc_stage2_jobs")
