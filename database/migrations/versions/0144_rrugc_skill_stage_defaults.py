"""Tenant skill notes, safe registry deletion, and per-stage defaults.

Revision ID: 0144_rrugc_skill_stage_defaults
Revises: 0143_rrugc_colorway_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0144_rrugc_skill_stage_defaults"
down_revision = "0143_rrugc_colorway_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_stage2_skill_registry", sa.Column("note", sa.Text(), nullable=False, server_default=""))
    op.add_column("rrugc_stage2_skill_registry", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("rrugc_stage2_jobs", sa.Column("regenerated_from_job_id", sa.String(36)))
    op.create_table(
        "rrugc_image_output_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("remote_file_id", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(128)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "stage", "job_id", "version", name="uq_rrugc_image_output_version"),
    )
    op.create_index("ix_rrugc_image_output_job", "rrugc_image_output_versions",
                    ["tenant_id", "stage", "job_id"])
    op.create_table(
        "rrugc_stage_skill_defaults",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("stage", sa.String(16), primary_key=True),
        sa.Column("registry_id", sa.String(36), sa.ForeignKey("rrugc_stage2_skill_registry.id", ondelete="SET NULL")),
        sa.Column("updated_by_user_id", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rrugc_stage_skill_defaults")
    op.drop_index("ix_rrugc_image_output_job", table_name="rrugc_image_output_versions")
    op.drop_table("rrugc_image_output_versions")
    op.drop_column("rrugc_stage2_jobs", "regenerated_from_job_id")
    op.drop_column("rrugc_stage2_skill_registry", "deleted_at")
    op.drop_column("rrugc_stage2_skill_registry", "note")
