"""Tenant-wide Cap Stack Scout control and unique Pinterest image library.

Revision ID: 0154_rrugc_cap_stack_scout
Revises: 0153_rrugc_stage1_font_pool
"""
from alembic import op
import sqlalchemy as sa

revision = "0154_rrugc_cap_stack_scout"
down_revision = "0153_rrugc_stage1_font_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_cap_stack_controls",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lease_agent_id", sa.String(36)),
        sa.Column("lease_run_id", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_scan_at", sa.DateTime(timezone=True)),
        sa.Column("run_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_result", sa.String(40)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "rrugc_cap_stack_images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("pin_key", sa.String(128), nullable=False),
        sa.Column("pin_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("alt_text", sa.Text()),
        sa.Column("source_query", sa.String(256)),
        sa.Column("discovered_by_agent_id", sa.String(36)),
        sa.Column("caps_detected", sa.Integer()),
        sa.Column("count_confidence", sa.Float()),
        sa.Column("count_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("manual_caps_count", sa.Integer()),
        sa.Column("manual_label", sa.String(12)),
        sa.Column("feedback_by", sa.String(255)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "pin_key", name="uq_rrugc_cap_stack_tenant_pin"),
    )
    op.create_index("ix_rrugc_cap_stack_status_created", "rrugc_cap_stack_images", ["tenant_id", "manual_label", "created_at"])
    op.create_index("ix_rrugc_cap_stack_source_query", "rrugc_cap_stack_images", ["tenant_id", "source_query"])


def downgrade() -> None:
    op.drop_index("ix_rrugc_cap_stack_source_query", table_name="rrugc_cap_stack_images")
    op.drop_index("ix_rrugc_cap_stack_status_created", table_name="rrugc_cap_stack_images")
    op.drop_table("rrugc_cap_stack_images")
    op.drop_table("rrugc_cap_stack_controls")
