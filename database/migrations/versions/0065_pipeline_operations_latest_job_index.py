"""add pipeline operations latest-job index

Revision ID: 0065_pipeline_operations_latest_job_index
Revises: 0064_dynamic_gemini_backups
"""

from alembic import op


revision = "0065_pipeline_operations_latest_job_index"
down_revision = "0064_dynamic_gemini_backups"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_processing_jobs_pipeline_latest",
        "processing_jobs",
        ["tenant_id", "job_type", "entity_type", "entity_id", "created_at", "updated_at"],
    )


def downgrade():
    op.drop_index("ix_processing_jobs_pipeline_latest", table_name="processing_jobs")
