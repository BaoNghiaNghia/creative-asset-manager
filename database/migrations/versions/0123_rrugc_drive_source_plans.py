"""Add Drive-backed RRUGC source plans.

Revision ID: 0123_rrugc_drive_source_plans
Revises: 0122_viewer_workflow_permissions
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0123_rrugc_drive_source_plans"
down_revision = "0122_viewer_workflow_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_source_plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("root_folder_id", sa.String(length=255), nullable=False),
        sa.Column("source_file_id", sa.String(length=255), nullable=False),
        sa.Column("source_parent_folder_id", sa.String(length=255)),
        sa.Column("source_relative_path", sa.Text(), nullable=False),
        sa.Column("source_name", sa.String(length=500), nullable=False),
        sa.Column("source_mime_type", sa.String(length=128), nullable=False),
        sa.Column("source_size_bytes", sa.Integer()),
        sa.Column("source_width", sa.Integer()),
        sa.Column("source_height", sa.Integer()),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("source_web_url", sa.Text()),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column(
            "analysis_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "target_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("20"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("visual_context_json", sa.JSON()),
        sa.Column("campaign_id", sa.String(length=36)),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_source_plan_campaign",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "root_folder_id",
            "source_file_id",
            name="uq_rrugc_source_plan_source",
        ),
    )
    op.create_index(
        "ix_rrugc_source_plan_tenant_status",
        "rrugc_source_plans",
        ["tenant_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_rrugc_source_plan_campaign",
        "rrugc_source_plans",
        ["tenant_id", "campaign_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_source_plan_campaign",
        table_name="rrugc_source_plans",
    )
    op.drop_index(
        "ix_rrugc_source_plan_tenant_status",
        table_name="rrugc_source_plans",
    )
    op.drop_table("rrugc_source_plans")
