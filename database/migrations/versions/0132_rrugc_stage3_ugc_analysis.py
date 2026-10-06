"""Add persistent Stage 3 UGC review analysis.

Revision ID: 0132_rrugc_stage3_ugc_analysis
Revises: 0131_rrugc_keyword_source_media
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0132_rrugc_stage3_ugc_analysis"
down_revision = "0131_rrugc_keyword_source_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rrugc_stage3_analyses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("stage2_job_id", sa.String(length=36), nullable=False),
        sa.Column("output_content_hash", sa.String(length=64), nullable=True),
        sa.Column("analysis_version", sa.String(length=64), nullable=False),
        sa.Column("analysis_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("processing_job_id", sa.String(length=36), nullable=True),
        sa.Column("people_count", sa.Integer(), nullable=True),
        sa.Column("person_visible", sa.Boolean(), nullable=True),
        sa.Column("hat_visible", sa.Boolean(), nullable=True),
        sa.Column("product_visible", sa.Boolean(), nullable=True),
        sa.Column("embroidery_visible", sa.Boolean(), nullable=True),
        sa.Column("mobile_ugc_score", sa.Float(), nullable=True),
        sa.Column("photorealism_score", sa.Float(), nullable=True),
        sa.Column("product_visibility_score", sa.Float(), nullable=True),
        sa.Column("review_fit_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("scene_type", sa.String(length=64), nullable=True),
        sa.Column("framing_type", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("reject_reasons_json", sa.JSON(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["stage2_job_id"],
            ["rrugc_stage2_jobs.id"],
            name="fk_rrugc_stage3_analysis_stage2_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "stage2_job_id",
            name="uq_rrugc_stage3_analysis_stage2_job",
        ),
    )
    op.create_index(
        "ix_rrugc_stage3_analysis_status_updated",
        "rrugc_stage3_analyses",
        ["tenant_id", "status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_stage3_analysis_status_updated",
        table_name="rrugc_stage3_analyses",
    )
    op.drop_table("rrugc_stage3_analyses")
