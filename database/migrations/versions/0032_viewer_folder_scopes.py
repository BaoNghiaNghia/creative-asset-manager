"""Add tenant-scoped viewer folder scopes.

Revision ID: 0032_viewer_folder_scopes
Revises: 0031_gemini_project_quota_state
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_viewer_folder_scopes"
down_revision = "0031_gemini_project_quota_state"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "viewer_folder_scopes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("tenant_membership_id", sa.String(length=36), nullable=False),
        sa.Column("external_source_id", sa.String(length=36), nullable=False),
        sa.Column("folder_external_id", sa.String(length=2048), nullable=False),
        sa.Column("folder_name", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_membership_id"], ["tenant_memberships.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tenant_membership_id", "external_source_id", "folder_external_id", name="uq_viewer_folder_scope"),
    )
    op.create_index("ix_viewer_folder_scope_membership", "viewer_folder_scopes", ["tenant_id", "tenant_membership_id"])

def downgrade() -> None:
    op.drop_index("ix_viewer_folder_scope_membership", table_name="viewer_folder_scopes")
    op.drop_table("viewer_folder_scopes")
