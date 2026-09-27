"""Add RRUGC export provenance and catalog registration.

Revision ID: 0100_rrugc_export_catalog
Revises: 0099_rrugc_review_handoff
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0100_rrugc_export_catalog"
down_revision = "0099_rrugc_review_handoff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("export_record_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("catalog_asset_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("exported_by_user_id", sa.String(length=255)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("exported_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "rrugc_exports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("generation_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("review_task_id", sa.String(length=36), nullable=False),
        sa.Column("catalog_asset_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=128)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("storage_provider", sa.String(length=64), nullable=False),
        sa.Column("remote_file_id", sa.String(length=255), nullable=False),
        sa.Column("remote_folder_id", sa.String(length=255)),
        sa.Column("web_url", sa.Text()),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_attempt_id"],
            ["rrugc_generation_attempts.id"],
            name="fk_rrugc_export_generation_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id"],
            ["rrugc_review_tasks.id"],
            name="fk_rrugc_export_review_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "catalog_asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_rrugc_export_catalog_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_attempt_id",
            name="uq_rrugc_export_attempt",
        ),
    )
    op.create_index(
        "ix_rrugc_export_campaign_status",
        "rrugc_exports",
        ["tenant_id", "campaign_id", "status", "exported_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_export_campaign_status",
        table_name="rrugc_exports",
    )
    op.drop_table("rrugc_exports")
    op.drop_column("rrugc_generation_attempts", "exported_at")
    op.drop_column("rrugc_generation_attempts", "exported_by_user_id")
    op.drop_column("rrugc_generation_attempts", "catalog_asset_id")
    op.drop_column("rrugc_generation_attempts", "export_record_id")
