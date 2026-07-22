"""Add AI Operations defaults and durable cancellation requests.

Revision ID: 0023_ai_operations_controls
Revises: 0022_ai_operations_indexes

Rollback: downgrade removes only operator defaults and outstanding cancellation
requests. Existing jobs and processing policies are preserved.
"""
from alembic import op
import sqlalchemy as sa


revision = "0023_ai_operations_controls"
down_revision = "0022_ai_operations_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_processing_policies", sa.Column("default_ai_provider", sa.String(length=64), nullable=True))
    op.add_column("tenant_processing_policies", sa.Column("default_ai_model", sa.String(length=255), nullable=True))
    op.add_column("processing_jobs", sa.Column("cancellation_requested", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("processing_jobs", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("processing_jobs", sa.Column("cancel_requested_by", sa.String(length=255), nullable=True))
    op.add_column("processing_jobs", sa.Column("cancellation_reason", sa.Text(), nullable=True))
    op.create_index(
        "ix_processing_jobs_cancel_eligibility",
        "processing_jobs",
        ["tenant_id", "cancellation_requested", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_cancel_eligibility", table_name="processing_jobs")
    op.drop_column("processing_jobs", "cancellation_reason")
    op.drop_column("processing_jobs", "cancel_requested_by")
    op.drop_column("processing_jobs", "cancel_requested_at")
    op.drop_column("processing_jobs", "cancellation_requested")
    op.drop_column("tenant_processing_policies", "default_ai_model")
    op.drop_column("tenant_processing_policies", "default_ai_provider")
