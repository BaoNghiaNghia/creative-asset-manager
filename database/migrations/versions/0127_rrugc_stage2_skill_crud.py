"""Add persistent Stage 2 skill registry and pinned bundle hashes.

Revision ID: 0127_rrugc_stage2_skill_crud
Revises: 0126_rrugc_stage2_skill_registry
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0127_rrugc_stage2_skill_crud"
down_revision = "0126_rrugc_stage2_skill_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_stage2_skill_registry",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("skill_id", sa.String(length=255)),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("workflow", sa.String(length=64), nullable=False, server_default="image_studio"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_version", sa.String(length=64)),
        sa.Column("latest_version", sa.String(length=64)),
        sa.Column("synced_version", sa.String(length=64)),
        sa.Column("sync_state", sa.String(length=32), nullable=False, server_default="not_synced"),
        sa.Column("validation_status", sa.String(length=32), nullable=False, server_default="valid"),
        sa.Column("bundle_sha256", sa.String(length=64)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_by_user_id", sa.String(length=255)),
        sa.Column("updated_by_user_id", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "source", "skill_name",
            name="uq_rrugc_stage2_skill_registry_name",
        ),
    )
    op.create_index(
        "ix_rrugc_stage2_skill_registry_tenant_enabled",
        "rrugc_stage2_skill_registry",
        ["tenant_id", "enabled", "updated_at"],
    )
    op.create_index(
        "ix_rrugc_stage2_skill_registry_skill_id",
        "rrugc_stage2_skill_registry",
        ["tenant_id", "skill_id"],
    )

    op.create_table(
        "rrugc_stage2_skill_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("registry_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="available"),
        sa.Column("bundle_sha256", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["registry_id"],
            ["rrugc_stage2_skill_registry.id"],
            name="fk_rrugc_stage2_skill_version_registry",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "registry_id", "version",
            name="uq_rrugc_stage2_skill_version",
        ),
    )
    op.create_index(
        "ix_rrugc_stage2_skill_version_registry",
        "rrugc_stage2_skill_versions",
        ["tenant_id", "registry_id", "created_at"],
    )

    op.add_column(
        "rrugc_stage2_jobs",
        sa.Column("skill_bundle_sha256", sa.String(length=64)),
    )


def downgrade() -> None:
    op.drop_column("rrugc_stage2_jobs", "skill_bundle_sha256")
    op.drop_index(
        "ix_rrugc_stage2_skill_version_registry",
        table_name="rrugc_stage2_skill_versions",
    )
    op.drop_table("rrugc_stage2_skill_versions")
    op.drop_index(
        "ix_rrugc_stage2_skill_registry_skill_id",
        table_name="rrugc_stage2_skill_registry",
    )
    op.drop_index(
        "ix_rrugc_stage2_skill_registry_tenant_enabled",
        table_name="rrugc_stage2_skill_registry",
    )
    op.drop_table("rrugc_stage2_skill_registry")
