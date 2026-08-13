"""Persist encrypted tenant-scoped Creative AI credentials.

Revision ID: 0043_creative_ai_cred
Revises: 0042_inventory_rbac_backfill
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_creative_ai_cred"
down_revision = "0042_inventory_rbac_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_batch_jobs", sa.Column("credential_fingerprint", sa.String(length=64)))
    op.add_column("ai_batch_jobs", sa.Column("credential_encrypted_secret", sa.Text()))
    op.add_column("ai_batch_jobs", sa.Column("credential_key_version", sa.String(length=64)))
    op.create_table(
        "creative_ai_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="gemini"),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("secret_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("secret_last4", sa.String(length=4), nullable=False),
        sa.Column("label", sa.String(length=255)),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_status", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=255)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_creative_ai_credentials_tenant_provider"),
        sa.CheckConstraint("provider = 'gemini'", name="ck_creative_ai_credentials_provider"),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_creative_ai_credentials_status"),
        sa.CheckConstraint("length(secret_fingerprint) = 64", name="ck_creative_ai_credentials_fingerprint"),
        sa.CheckConstraint("length(secret_last4) = 4", name="ck_creative_ai_credentials_last4"),
    )
    op.create_index("ix_creative_ai_credentials_tenant_status", "creative_ai_credentials", ["tenant_id", "status"])
    op.create_table(
        "creative_ai_credential_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="gemini"),
        sa.Column("actor_id", sa.String(length=255)),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("previous_fingerprint", sa.String(length=64)),
        sa.Column("new_fingerprint", sa.String(length=64)),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_creative_ai_credential_audits_tenant_created", "creative_ai_credential_audits", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_creative_ai_credential_audits_tenant_created", table_name="creative_ai_credential_audits")
    op.drop_table("creative_ai_credential_audits")
    op.drop_index("ix_creative_ai_credentials_tenant_status", table_name="creative_ai_credentials")
    op.drop_table("creative_ai_credentials")
    op.drop_column("ai_batch_jobs", "credential_key_version")
    op.drop_column("ai_batch_jobs", "credential_encrypted_secret")
    op.drop_column("ai_batch_jobs", "credential_fingerprint")
