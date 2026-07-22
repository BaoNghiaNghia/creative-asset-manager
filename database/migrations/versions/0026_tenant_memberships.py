"""Add durable tenants, memberships, and active tenant sessions.

Revision ID: 0026_tenant_memberships
Revises: 0025_application_users

Rollback removes only AUTH-02 membership data and the nullable active-tenant
session pointer. Provider connection tenant identifiers and legacy actor
session fields are intentionally preserved for rolling-deployment safety.
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_tenant_memberships"
down_revision = "0025_application_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active','suspended','disabled')", name="ck_tenants_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('invited','active','suspended','removed')", name="ck_tenant_memberships_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_memberships_tenant_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_tenant_memberships_user_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], name="fk_tenant_memberships_invited_by", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
    )
    op.create_index("ix_tenant_memberships_user_status", "tenant_memberships", ["user_id", "status"])
    op.create_index("ix_tenant_memberships_tenant_status", "tenant_memberships", ["tenant_id", "status"])
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.add_column(sa.Column("active_tenant_id", sa.String(length=255), nullable=True))
        batch_op.create_foreign_key("fk_auth_sessions_active_tenant_id", "tenants", ["active_tenant_id"], ["id"], ondelete="SET NULL")
        batch_op.create_index("ix_auth_sessions_active_tenant_id", ["active_tenant_id"])


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.drop_index("ix_auth_sessions_active_tenant_id")
        batch_op.drop_constraint("fk_auth_sessions_active_tenant_id", type_="foreignkey")
        batch_op.drop_column("active_tenant_id")
    op.drop_index("ix_tenant_memberships_tenant_status", table_name="tenant_memberships")
    op.drop_index("ix_tenant_memberships_user_status", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_table("tenants")
