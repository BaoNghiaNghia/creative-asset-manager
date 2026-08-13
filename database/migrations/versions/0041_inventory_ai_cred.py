"""Add encrypted Inventory AI credentials.

Revision ID: 0041_inventory_ai_cred
Revises: 0040_inventory_export_archive
"""
from alembic import op
import sqlalchemy as sa
revision="0041_inventory_ai_cred"
down_revision="0040_inventory_export_archive"
branch_labels=None
depends_on=None
def upgrade():
 op.create_table("inventory_ai_credentials",sa.Column("id",sa.String(36),nullable=False),sa.Column("tenant_id",sa.String(255),nullable=False),sa.Column("provider",sa.String(64),nullable=False),sa.Column("encrypted_secret",sa.Text(),nullable=False),sa.Column("key_version",sa.String(64),nullable=False),sa.Column("secret_fingerprint",sa.String(64),nullable=False),sa.Column("secret_last4",sa.String(4),nullable=False),sa.Column("label",sa.String(255)),sa.Column("status",sa.String(32),nullable=False,server_default="active"),sa.Column("last_tested_at",sa.DateTime(timezone=True)),sa.Column("last_test_status",sa.String(64)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_by",sa.String(255)),sa.ForeignKeyConstraint(["tenant_id"],["tenants.id"],ondelete="CASCADE"),sa.PrimaryKeyConstraint("id"),sa.UniqueConstraint("tenant_id","provider",name="uq_inventory_ai_credentials_tenant_provider"),sa.CheckConstraint("provider = 'gemini'",name="ck_inventory_ai_credentials_provider"),sa.CheckConstraint("status IN ('active','disabled')",name="ck_inventory_ai_credentials_status"),sa.CheckConstraint("length(secret_fingerprint) = 64",name="ck_inventory_ai_credentials_fingerprint"),sa.CheckConstraint("length(secret_last4) = 4",name="ck_inventory_ai_credentials_last4"))
 op.create_index("ix_inventory_ai_credentials_tenant_status","inventory_ai_credentials",["tenant_id","status"])
 op.create_table("inventory_ai_credential_audits",sa.Column("id",sa.String(36),nullable=False),sa.Column("tenant_id",sa.String(255),nullable=False),sa.Column("provider",sa.String(64),nullable=False,server_default="gemini"),sa.Column("actor_id",sa.String(255)),sa.Column("action",sa.String(64),nullable=False),sa.Column("previous_fingerprint",sa.String(64)),sa.Column("new_fingerprint",sa.String(64)),sa.Column("result",sa.String(64),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["tenant_id"],["tenants.id"],ondelete="CASCADE"),sa.PrimaryKeyConstraint("id"))
 op.create_index("ix_inventory_ai_credential_audits_tenant_created","inventory_ai_credential_audits",["tenant_id","created_at"])
def downgrade():
 op.drop_index("ix_inventory_ai_credential_audits_tenant_created",table_name="inventory_ai_credential_audits");op.drop_table("inventory_ai_credential_audits")
 op.drop_index("ix_inventory_ai_credentials_tenant_status",table_name="inventory_ai_credentials");op.drop_table("inventory_ai_credentials")
