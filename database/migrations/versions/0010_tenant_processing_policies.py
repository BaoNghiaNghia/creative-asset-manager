"""Add tenant/provider rollout policy, concurrency and audit state.

Revision ID: 0010_tenant_processing_policies
Revises: 0009_durable_asset_pipeline

Rollback: disable PROCESSING_JOBS_ENABLED and drain workers before downgrade.
Export policy/audit rows if required. Queued jobs are preserved, but provider
classification and concurrency-accounting markers are removed.
"""

from alembic import op
import sqlalchemy as sa

revision = "0010_tenant_processing_policies"
down_revision = "0009_durable_asset_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_processing_policies",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("pipeline_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("managed_storage_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ai_analysis_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("search_v2_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sidecar_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processing_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pause_reason", sa.Text()),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("paused_by", sa.String(255)),
        sa.Column("rollout_mode", sa.String(32), nullable=False, server_default="explicit"),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("total_active_jobs_limit", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("ai_active_jobs_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_active_jobs_limit", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("storage_active_jobs_limit", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("total_active_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_active_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_active_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_active_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rollout_mode IN ('explicit', 'percentage')", name="ck_tenant_policy_rollout_mode"),
        sa.CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="ck_tenant_policy_rollout_percentage"),
        sa.CheckConstraint("total_active_jobs_limit > 0", name="ck_tenant_policy_total_limit"),
        sa.CheckConstraint("ai_active_jobs_limit > 0", name="ck_tenant_policy_ai_limit"),
        sa.CheckConstraint("source_active_jobs_limit > 0", name="ck_tenant_policy_source_limit"),
        sa.CheckConstraint("storage_active_jobs_limit > 0", name="ck_tenant_policy_storage_limit"),
        sa.CheckConstraint("total_active_jobs >= 0", name="ck_tenant_policy_total_active"),
        sa.CheckConstraint("ai_active_jobs >= 0", name="ck_tenant_policy_ai_active"),
        sa.CheckConstraint("source_active_jobs >= 0", name="ck_tenant_policy_source_active"),
        sa.CheckConstraint("storage_active_jobs >= 0", name="ck_tenant_policy_storage_active"),
    )
    op.create_index("ix_tenant_processing_policies_eligible", "tenant_processing_policies", ["processing_paused", "pipeline_enabled", "tenant_id"])
    op.create_table(
        "tenant_provider_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("provider_scope", sa.String(32), nullable=False),
        sa.Column("processing_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("processing_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pause_reason", sa.Text()),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("paused_by", sa.String(255)),
        sa.Column("active_jobs_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_jobs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider_key", "provider_scope", name="uq_tenant_provider_policy_identity"),
        sa.CheckConstraint("active_jobs_limit > 0", name="ck_tenant_provider_policy_limit"),
        sa.CheckConstraint("active_jobs >= 0", name="ck_tenant_provider_policy_active"),
    )
    op.create_index("ix_tenant_provider_policy_eligible", "tenant_provider_policies", ["tenant_id", "provider_key", "provider_scope", "processing_paused"])
    op.create_table(
        "processing_policy_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("provider_key", sa.String(64)),
        sa.Column("provider_scope", sa.String(32)),
        sa.Column("reason", sa.Text()),
        sa.Column("old_policy_json", sa.JSON(), nullable=False),
        sa.Column("new_policy_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processing_policy_audits_tenant_created", "processing_policy_audits", ["tenant_id", "created_at"])
    op.add_column("processing_jobs", sa.Column("provider_key", sa.String(64)))
    op.add_column("processing_jobs", sa.Column("provider_scope", sa.String(32)))
    op.add_column("processing_jobs", sa.Column("concurrency_accounted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_processing_jobs_policy_claim", "processing_jobs", ["tenant_id", "job_type", "provider_key", "provider_scope", "status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_policy_claim", table_name="processing_jobs")
    op.drop_column("processing_jobs", "concurrency_accounted")
    op.drop_column("processing_jobs", "provider_scope")
    op.drop_column("processing_jobs", "provider_key")
    op.drop_index("ix_processing_policy_audits_tenant_created", table_name="processing_policy_audits")
    op.drop_table("processing_policy_audits")
    op.drop_index("ix_tenant_provider_policy_eligible", table_name="tenant_provider_policies")
    op.drop_table("tenant_provider_policies")
    op.drop_index("ix_tenant_processing_policies_eligible", table_name="tenant_processing_policies")
    op.drop_table("tenant_processing_policies")
