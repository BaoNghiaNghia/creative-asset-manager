"""Add durable image generation runs.

Revision ID: 0053_image_generation_runs
Revises: 0052_video_gemini_credential
"""
import sqlalchemy as sa
from alembic import op

revision = "0053_image_generation_runs"
down_revision = "0052_video_gemini_credential"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_asset_id", sa.String(36), nullable=False),
        sa.Column("source_source_asset_id", sa.String(36)),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_model", sa.String(128)),
        sa.Column("preservation_mode", sa.String(32), nullable=False),
        sa.Column("target_width", sa.Integer, nullable=False),
        sa.Column("target_height", sa.Integer, nullable=False),
        sa.Column("source_width", sa.Integer, nullable=False),
        sa.Column("source_height", sa.Integer, nullable=False),
        sa.Column("normalized_width", sa.Integer, nullable=False),
        sa.Column("normalized_height", sa.Integer, nullable=False),
        sa.Column("prompt", sa.Text),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider_upload_id", sa.String(255)),
        sa.Column("provider_job_id", sa.String(255)),
        sa.Column("provider_interaction_id", sa.String(255)),
        sa.Column("provider_status_url", sa.Text),
        sa.Column("provider_cancel_url", sa.Text),
        sa.Column("output_asset_id", sa.String(36)),
        sa.Column("client_request_id", sa.String(36), nullable=False),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("operation = 'square_expand'", name="ck_image_generation_runs_operation"),
        sa.CheckConstraint("provider IN ('adobe_firefly', 'gemini')", name="ck_image_generation_runs_provider"),
        sa.CheckConstraint("preservation_mode IN ('strict_expand', 'semantic_expand')", name="ck_image_generation_runs_preservation"),
        sa.CheckConstraint("target_width IN (1024, 2048) AND target_height = target_width", name="ck_image_generation_runs_target"),
        sa.CheckConstraint("status IN ('queued', 'preparing', 'submitted', 'running', 'storing', 'completed', 'failed')", name="ck_image_generation_runs_status"),
        sa.ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["assets.tenant_id", "assets.id"], ondelete="RESTRICT", name="fk_image_generation_runs_source_asset"),
        sa.ForeignKeyConstraint(["tenant_id", "output_asset_id"], ["assets.tenant_id", "assets.id"], ondelete="RESTRICT", name="fk_image_generation_runs_output_asset"),
        sa.UniqueConstraint("tenant_id", "created_by_user_id", "client_request_id", name="uq_image_generation_runs_client_request"),
    )
    op.create_index("ix_image_generation_runs_tenant_status", "image_generation_runs", ["tenant_id", "status", "created_at"])
    op.create_index("ix_image_generation_runs_source", "image_generation_runs", ["tenant_id", "source_asset_id", "created_at"])


def downgrade() -> None:
    op.drop_table("image_generation_runs")
