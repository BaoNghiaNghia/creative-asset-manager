"""Add tenant-scoped roles and permissions.

Revision ID: 0027_tenant_rbac
Revises: 0026_tenant_memberships

The migration creates only tenant RBAC records. Platform administrators remain
outside these tables. Downgrade removes assignments/catalog data and the
composite membership key without changing users, tenants or memberships.
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_tenant_rbac"
down_revision = "0026_tenant_memberships"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_memberships") as batch_op:
        batch_op.create_unique_constraint(
            "uq_tenant_memberships_tenant_id_id", ["tenant_id", "id"]
        )
    op.create_table(
        "permissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("permission_key", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_permissions_status"),
        sa.CheckConstraint("length(trim(permission_key)) > 0", name="ck_permissions_key_not_empty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("permission_key", name="uq_permissions_key"),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("role_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("protected", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_roles_status"),
        sa.CheckConstraint("length(trim(role_key)) > 0", name="ck_roles_key_not_empty"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_roles_name_not_empty"),
        sa.CheckConstraint("is_system = false OR protected = true", name="ck_system_roles_protected"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_roles_tenant_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_roles_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "role_key", name="uq_roles_tenant_key"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index("ix_roles_tenant_status", "roles", ["tenant_id", "status"])
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("permission_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_role_permissions_role_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], name="fk_role_permissions_permission_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
    )
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"])
    op.create_table(
        "membership_roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("tenant_membership_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tenant_membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.id"],
            name="fk_membership_roles_tenant_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "role_id"],
            ["roles.tenant_id", "roles.id"],
            name="fk_membership_roles_tenant_role",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_membership_id", "role_id", name="uq_membership_roles_membership_role"),
    )
    op.create_index(
        "ix_membership_roles_tenant_membership",
        "membership_roles",
        ["tenant_id", "tenant_membership_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_membership_roles_tenant_membership", table_name="membership_roles")
    op.drop_table("membership_roles")
    op.drop_index("ix_role_permissions_permission_id", table_name="role_permissions")
    op.drop_table("role_permissions")
    op.drop_index("ix_roles_tenant_status", table_name="roles")
    op.drop_table("roles")
    op.drop_table("permissions")
    with op.batch_alter_table("tenant_memberships") as batch_op:
        batch_op.drop_constraint("uq_tenant_memberships_tenant_id_id", type_="unique")
