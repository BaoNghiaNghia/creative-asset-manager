"""Durable keyword Skill image generation after RRUGC Stage 0.

Revision ID: 0142_rrugc_keyword_image_jobs
Revises: 0141_rrugc_query_intelligence
"""
from alembic import op
import sqlalchemy as sa

revision = "0142_rrugc_keyword_image_jobs"
down_revision = "0141_rrugc_query_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_keyword_image_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("keyword_id", sa.String(36), nullable=False),
        sa.Column("keyword_text", sa.String(500), nullable=False),
        sa.Column("skill_source", sa.String(32), nullable=False),
        sa.Column("skill_id", sa.String(255)),
        sa.Column("skill_name", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.String(64)),
        sa.Column("skill_bundle_sha256", sa.String(64)),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("output_remote_file_id", sa.String(255)),
        sa.Column("output_content_type", sa.String(128)),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("output_width", sa.Integer()),
        sa.Column("output_height", sa.Integer()),
        sa.Column("output_web_url", sa.Text()),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["keyword_id"], ["rrugc_keyword_volumes.id"],
                                name="fk_rrugc_keyword_image_job_keyword", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "keyword_id", name="uq_rrugc_keyword_image_job_keyword"),
    )
    op.create_index("ix_rrugc_keyword_image_job_status", "rrugc_keyword_image_jobs",
                    ["tenant_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_rrugc_keyword_image_job_status", table_name="rrugc_keyword_image_jobs")
    op.drop_table("rrugc_keyword_image_jobs")
