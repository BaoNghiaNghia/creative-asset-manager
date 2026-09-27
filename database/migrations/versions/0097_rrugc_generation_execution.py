"""Execute RRUGC generation attempts with durable output provenance.

Revision ID: 0097_rrugc_generation_execution
Revises: 0096_rrugc_generation_foundation
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0097_rrugc_generation_execution"
down_revision = "0096_rrugc_generation_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("candidate_snapshot_json", sa.JSON()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("provider_request_id", sa.String(length=255)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("processing_job_id", sa.String(length=36)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_content_hash", sa.String(length=64)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_content_type", sa.String(length=128)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_size_bytes", sa.Integer()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_width", sa.Integer()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_height", sa.Integer()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_remote_file_id", sa.String(length=255)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_remote_folder_id", sa.String(length=255)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("output_web_url", sa.Text()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("last_error_code", sa.String(length=100)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("last_error_message", sa.Text()),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("queued_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_generation_attempts",
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_rrugc_generation_output_hash",
        "rrugc_generation_attempts",
        ["tenant_id", "output_content_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_generation_output_hash",
        table_name="rrugc_generation_attempts",
    )
    for name in (
        "completed_at",
        "started_at",
        "queued_at",
        "last_error_message",
        "last_error_code",
        "output_web_url",
        "output_remote_folder_id",
        "output_remote_file_id",
        "output_height",
        "output_width",
        "output_size_bytes",
        "output_content_type",
        "output_content_hash",
        "processing_job_id",
        "provider_request_id",
        "candidate_snapshot_json",
    ):
        op.drop_column("rrugc_generation_attempts", name)
