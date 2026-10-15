"""Branding DNA foundation registry, no browser jobs enabled.

Revision ID: 0157_branding_dna_foundation
Revises: 0156_rrugc_cap_stack_2_to_6
"""
from alembic import op
import sqlalchemy as sa

revision = "0157_branding_dna_foundation"
down_revision = "0156_rrugc_cap_stack_2_to_6"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("bc_shops",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("region", sa.String(20), nullable=False),
        sa.Column("canonical_shop_key", sa.String(255), nullable=False),
        sa.Column("storefront_url", sa.String(2048), nullable=False),
        sa.Column("display_name", sa.String(90), nullable=False),
        sa.Column("access_mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "platform", "region", "canonical_shop_key",
                            name="uq_bc_shop_tenant_platform_region"),
    )
    op.create_index("ix_bc_shops_tenant_updated", "bc_shops", ["tenant_id", "updated_at"])
    op.create_table("bc_browser_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("label", sa.String(90), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="unregistered"),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bc_agent_tenant_status", "bc_browser_agents", ["tenant_id", "status"])

def downgrade() -> None:
    op.drop_index("ix_bc_agent_tenant_status", table_name="bc_browser_agents")
    op.drop_table("bc_browser_agents")
    op.drop_index("ix_bc_shops_tenant_updated", table_name="bc_shops")
    op.drop_table("bc_shops")
