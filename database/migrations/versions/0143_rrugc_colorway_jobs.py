"""Durable 13-color embroidery generation without changing existing stage semantics.

Revision ID: 0143_rrugc_colorway_jobs
Revises: 0142_rrugc_keyword_image_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0143_rrugc_colorway_jobs"
down_revision = "0142_rrugc_keyword_image_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_colorway_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_plan_id", sa.String(36), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("color_key", sa.String(80), nullable=False),
        sa.Column("stock_sha256", sa.String(64), nullable=False),
        sa.Column("skill_source", sa.String(32), nullable=False),
        sa.Column("skill_id", sa.String(255)),
        sa.Column("skill_name", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.String(64)),
        sa.Column("skill_bundle_sha256", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_remote_file_id", sa.String(255)),
        sa.Column("output_content_type", sa.String(128)),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["source_plan_id"], ["rrugc_source_plans.id"],
                                name="fk_rrugc_colorway_source", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "source_plan_id", "source_revision", "color_key",
                            name="uq_rrugc_colorway_source_revision_color"),
    )
    op.create_index("ix_rrugc_colorway_status", "rrugc_colorway_jobs",
                    ["tenant_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_rrugc_colorway_status", table_name="rrugc_colorway_jobs")
    op.drop_table("rrugc_colorway_jobs")
