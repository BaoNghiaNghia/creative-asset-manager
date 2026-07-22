"""Add durable platform administrator assignments.

Revision ID: 0028_central_authorization
Revises: 0027_tenant_rbac

Platform privilege is deliberately independent from tenant roles. Downgrade
removes only platform assignments; tenant memberships and RBAC are preserved.
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_central_authorization"
down_revision = "0027_tenant_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_admin_assignments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_platform_admin_assignments_status"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_platform_admin_assignments_user", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], name="fk_platform_admin_assignments_granted_by", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_platform_admin_assignments_user"),
    )
    op.create_index(
        "ix_platform_admin_assignments_status",
        "platform_admin_assignments",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_admin_assignments_status",
        table_name="platform_admin_assignments",
    )
    op.drop_table("platform_admin_assignments")
