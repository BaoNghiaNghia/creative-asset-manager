"""Optional member-scoped navbar access (NULL retains legacy RBAC).

Revision ID: 0187_navbar_member_permissions
Revises: 0186_rrugc_stage1_front_design_size
"""
from alembic import op
import sqlalchemy as sa

revision = "0187_navbar_member_permissions"
down_revision = "0186_rrugc_stage1_front_design_size"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tenant_memberships") as batch:
        batch.add_column(sa.Column("navigation_modules_json", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("tenant_memberships") as batch:
        batch.drop_column("navigation_modules_json")
