"""inventory prompt versions

Revision ID: 0071_inventory_prompt_versions
Revises: 0070_inventory_daily_carry_forward
"""
from alembic import op
import sqlalchemy as sa

revision = "0071_inventory_prompt_versions"
down_revision = "0070_inventory_daily_carry_forward"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("inventory_prompt_versions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(255), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_type", sa.String(64), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("created_by", sa.String(255)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("activated_by", sa.String(255)), sa.Column("activated_at", sa.DateTime(timezone=True)), sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "prompt_type", "version", name="uq_inventory_prompt_version"),
        sa.CheckConstraint("version > 0", name="ck_inventory_prompt_version_positive"), sa.CheckConstraint("prompt_type IN ('daily_gemini_processing','carry_forward_0900')", name="ck_inventory_prompt_type"), sa.CheckConstraint("status IN ('draft','active','archived')", name="ck_inventory_prompt_status"),
    )
    op.create_index("ix_inventory_prompt_active", "inventory_prompt_versions", ["tenant_id", "prompt_type", "status"])
    op.create_index("uq_inventory_prompt_one_active", "inventory_prompt_versions", ["tenant_id", "prompt_type"], unique=True, postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    for table, prefix in (("inventory_daily_sheet_snapshots", "gemini_prompt"), ("inventory_daily_carry_forwards", "prompt")):
        op.add_column(table, sa.Column(f"{prefix}_source", sa.String(32)))
        op.add_column(table, sa.Column(f"{prefix}_version", sa.String(64)))
        op.add_column(table, sa.Column(f"{prefix}_hash", sa.String(64)))

def downgrade():
    for table, prefix in (("inventory_daily_carry_forwards", "prompt"), ("inventory_daily_sheet_snapshots", "gemini_prompt")):
        op.drop_column(table, f"{prefix}_hash"); op.drop_column(table, f"{prefix}_version"); op.drop_column(table, f"{prefix}_source")
    op.drop_index("uq_inventory_prompt_one_active", table_name="inventory_prompt_versions")
    op.drop_index("ix_inventory_prompt_active", table_name="inventory_prompt_versions")
    op.drop_table("inventory_prompt_versions")
