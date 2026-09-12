"""add CAM video generation domain and durable storage classification.

Revision ID: 0067_video_generation_domain
Revises: 0066_video_gemini_backups
"""

import sqlalchemy as sa
from alembic import op

revision = "0067_video_generation_domain"
down_revision = "0066_video_gemini_backups"
branch_labels = None
depends_on = None


def upgrade():
    # Generated videos are durable application outputs, not analysis staging.
    with op.batch_alter_table("asset_storage_objects") as batch:
        batch.add_column(
            sa.Column("storage_class", sa.String(length=32), nullable=False, server_default="staging")
        )
        batch.create_check_constraint(
            "ck_asset_storage_objects_storage_class",
            "storage_class IN ('staging', 'durable')",
        )
    op.create_table(
        "video_generation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_model", sa.String(128), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("aspect_ratio", sa.String(16), nullable=False),
        sa.Column("duration_seconds", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("client_request_id", sa.String(200), nullable=False),
        sa.Column("created_by_user_id", sa.String(255), nullable=False),
        sa.Column("gateway_generation_id", sa.String(255)),
        sa.Column("output_asset_id", sa.String(36)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id", "output_asset_id"], ["assets.tenant_id", "assets.id"], name="fk_video_generation_output"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_video_generation_tenant_id"),
        sa.UniqueConstraint("tenant_id", "created_by_user_id", "client_request_id", name="uq_video_generation_client_request"),
        sa.CheckConstraint("provider = 'dola'", name="ck_video_generation_provider"),
        sa.CheckConstraint("status IN ('queued','preparing','submitted','running','submission_unknown','storing','completed','failed','cancelled')", name="ck_video_generation_status"),
    )
    op.create_index("ix_video_generation_tenant_status", "video_generation_runs", ["tenant_id", "status", "created_at"])
    op.create_table(
        "video_generation_references",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "run_id"], ["video_generation_runs.tenant_id", "video_generation_runs.id"], name="fk_video_generation_reference_run", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], name="fk_video_generation_reference_asset"),
        sa.UniqueConstraint("run_id", "position", name="uq_video_generation_reference_position"),
    )


def downgrade():
    op.drop_table("video_generation_references")
    op.drop_index("ix_video_generation_tenant_status", table_name="video_generation_runs")
    op.drop_table("video_generation_runs")
    with op.batch_alter_table("asset_storage_objects") as batch:
        batch.drop_constraint("ck_asset_storage_objects_storage_class", type_="check")
        batch.drop_column("storage_class")
