"""Persist tenant-safe product-folder Markdown notes.

Revision ID: 0044_folder_notes
Revises: 0043_creative_ai_cred
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_folder_notes"
down_revision = "0043_creative_ai_cred"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folder_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("external_source_id", sa.String(length=36), nullable=False),
        sa.Column("folder_external_id", sa.String(length=2048), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "external_source_id"], ["external_sources.tenant_id", "external_sources.id"], ondelete="CASCADE", name="fk_folder_notes_tenant_source"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_source_id", "folder_external_id", name="uq_folder_notes_folder_identity"),
    )
    op.create_index("ix_folder_notes_tenant_source_folder", "folder_notes", ["tenant_id", "external_source_id", "folder_external_id"])


def downgrade() -> None:
    op.drop_index("ix_folder_notes_tenant_source_folder", table_name="folder_notes")
    op.drop_table("folder_notes")
