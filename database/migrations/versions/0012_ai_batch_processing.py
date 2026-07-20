"""Add durable AI batch jobs and items.

Revision ID: 0012_ai_batch_processing
Revises: 0011_ai_governance_pilot

Rollback: disable AI_BATCH_ANALYSIS_ENABLED, stop/drain batch workers, export
batch audit state, then downgrade. Analyses and indexed projections are retained.
"""
from alembic import op
import sqlalchemy as sa

revision="0012_ai_batch_processing"
down_revision="0011_ai_governance_pilot"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        "ai_batch_jobs",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("tenant_id",sa.String(255),nullable=False),
        sa.Column("submission_key",sa.String(512),nullable=False),
        sa.Column("provider",sa.String(100),nullable=False),
        sa.Column("model",sa.String(255),nullable=False),
        sa.Column("metadata_profile_id",sa.String(36),nullable=False),
        sa.Column("metadata_profile",sa.String(255),nullable=False),
        sa.Column("metadata_profile_version",sa.String(100),nullable=False),
        sa.Column("prompt_version",sa.String(100),nullable=False),
        sa.Column("pipeline_version",sa.String(100),nullable=False),
        sa.Column("provider_batch_id",sa.String(512)),
        sa.Column("provider_request_id",sa.String(255)),
        sa.Column("status",sa.String(24),nullable=False),
        sa.Column("item_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("completed_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("failed_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("missing_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("submission_attempt",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("poll_attempt",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("import_attempt",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("result_cursor",sa.String(512)),
        sa.Column("input_checksum",sa.String(64)),
        sa.Column("input_bytes",sa.BigInteger(),nullable=False,server_default="0"),
        sa.Column("estimated_cost_micros",sa.BigInteger(),nullable=False,server_default="0"),
        sa.Column("actual_cost_micros",sa.BigInteger(),nullable=False,server_default="0"),
        sa.Column("currency",sa.String(3),nullable=False,server_default="USD"),
        sa.Column("usage_json",sa.JSON(),nullable=False),
        sa.Column("cancellation_requested",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("next_poll_at",sa.DateTime(timezone=True)),
        sa.Column("last_error_code",sa.String(100)),
        sa.Column("last_error_message",sa.Text()),
        sa.Column("error_json",sa.JSON()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("submitted_at",sa.DateTime(timezone=True)),
        sa.Column("completed_at",sa.DateTime(timezone=True)),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["tenant_id","metadata_profile_id"],
            ["metadata_profiles.tenant_id","metadata_profiles.id"],
            ondelete="RESTRICT",name="fk_ai_batch_jobs_tenant_profile"),
        sa.UniqueConstraint("tenant_id","id",name="uq_ai_batch_jobs_tenant_id"),
        sa.UniqueConstraint("tenant_id","submission_key",name="uq_ai_batch_jobs_submission"),
        sa.UniqueConstraint("provider","provider_batch_id",name="uq_ai_batch_jobs_provider_id"),
        sa.CheckConstraint("status IN ('preparing','submitting','submitted','running','importing','completed','partial_failed','failed','expired','cancelled','ambiguous')",name="ck_ai_batch_jobs_status"),
        sa.CheckConstraint("item_count >= 0 AND completed_count >= 0 AND failed_count >= 0 AND missing_count >= 0",name="ck_ai_batch_jobs_counts"),
    )
    op.create_index("ix_ai_batch_jobs_tenant_status","ai_batch_jobs",["tenant_id","status","next_poll_at"])
    op.create_table(
        "ai_batch_items",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("tenant_id",sa.String(255),nullable=False),
        sa.Column("batch_job_id",sa.String(36),nullable=False),
        sa.Column("custom_item_id",sa.String(128),nullable=False),
        sa.Column("asset_id",sa.String(36),nullable=False),
        sa.Column("analysis_id",sa.String(36),nullable=False),
        sa.Column("provider_item_id",sa.String(255)),
        sa.Column("status",sa.String(24),nullable=False),
        sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("result_received",sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column("result_sequence",sa.Integer()),
        sa.Column("budget_operation_key",sa.String(512)),
        sa.Column("budget_reservation_id",sa.String(36)),
        sa.Column("estimated_cost_micros",sa.BigInteger(),nullable=False,server_default="0"),
        sa.Column("actual_cost_micros",sa.BigInteger(),nullable=False,server_default="0"),
        sa.Column("usage_json",sa.JSON(),nullable=False),
        sa.Column("last_error_code",sa.String(100)),
        sa.Column("last_error_message",sa.Text()),
        sa.Column("error_json",sa.JSON()),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("submitted_at",sa.DateTime(timezone=True)),
        sa.Column("completed_at",sa.DateTime(timezone=True)),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["tenant_id","batch_job_id"],
            ["ai_batch_jobs.tenant_id","ai_batch_jobs.id"],
            ondelete="CASCADE",name="fk_ai_batch_items_tenant_batch"),
        sa.ForeignKeyConstraint(["tenant_id","asset_id"],
            ["assets.tenant_id","assets.id"],ondelete="RESTRICT",
            name="fk_ai_batch_items_tenant_asset"),
        sa.ForeignKeyConstraint(["analysis_id"],["asset_ai_analyses.id"],
            ondelete="RESTRICT",name="fk_ai_batch_items_analysis"),
        sa.UniqueConstraint("batch_job_id","custom_item_id",name="uq_ai_batch_items_custom"),
        sa.UniqueConstraint("batch_job_id","analysis_id",name="uq_ai_batch_items_analysis"),
        sa.UniqueConstraint("tenant_id","analysis_id",name="uq_ai_batch_items_tenant_analysis"),
        sa.CheckConstraint("status IN ('pending','prepared','submitted','completed','failed','missing','cancelled','budget_blocked')",name="ck_ai_batch_items_status"),
        sa.CheckConstraint("attempt_count >= 0",name="ck_ai_batch_items_attempt"),
    )
    op.create_index("ix_ai_batch_items_batch_status","ai_batch_items",["batch_job_id","status","id"])

def downgrade():
    op.drop_index("ix_ai_batch_items_batch_status",table_name="ai_batch_items")
    op.drop_table("ai_batch_items")
    op.drop_index("ix_ai_batch_jobs_tenant_status",table_name="ai_batch_jobs")
    op.drop_table("ai_batch_jobs")
