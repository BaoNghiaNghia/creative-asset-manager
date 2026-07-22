"""Add canonical application users and external identities.

Revision ID: 0025_application_users
Revises: 0024_ai_operations_configuration

This additive migration preserves legacy OAuth tenant/actor fields. Downgrade
removes only nullable session user references and AUTH-01 identity records; it
does not alter encrypted OAuth connections, legacy sessions, or tenant data.
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_application_users"
down_revision = "0024_ai_operations_configuration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("primary_email", sa.String(length=512), nullable=True),
        sa.Column("display_name", sa.String(length=512), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active','suspended','disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_primary_email", "users", ["primary_email"])

    op.create_table(
        "user_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.String(length=512), nullable=False),
        sa.Column("provider_email", sa.String(length=512), nullable=True),
        sa.Column("provider_tenant_id", sa.String(length=512), nullable=True),
        sa.Column("provider_metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('google','microsoft')", name="ck_user_identities_provider"),
        sa.CheckConstraint("length(trim(provider_subject)) > 0", name="ck_user_identities_subject_not_empty"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_identities_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_user_identities_provider_subject"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])

    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_auth_sessions_user_id", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("ix_auth_sessions_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.drop_index("ix_auth_sessions_user_id")
        batch_op.drop_constraint("fk_auth_sessions_user_id", type_="foreignkey")
        batch_op.drop_column("user_id")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_index("ix_users_primary_email", table_name="users")
    op.drop_table("users")
