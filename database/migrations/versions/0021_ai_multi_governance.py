"""Add multi-provider AI production governance.

Revision ID: 0021_ai_multi_governance
Revises: 0020_ai_analysis_requests

Rollback: stop AI workers, remove runtime controls/overrides, then downgrade.
Existing usage and audit records remain available before downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_ai_multi_governance"
down_revision = "0020_ai_analysis_requests"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("ai_cost_rates") as batch:
        batch.drop_constraint("uq_ai_cost_rate_version", type_="unique")
        batch.add_column(sa.Column("processing_mode", sa.String(16), nullable=False, server_default="any"))
        batch.create_check_constraint("ck_ai_cost_rate_mode", "processing_mode IN ('any','single','batch')")
        batch.create_unique_constraint("uq_ai_cost_rate_version", ["provider","model","processing_mode","effective_at"])

    with op.batch_alter_table("ai_budget_reservations") as batch:
        batch.add_column(sa.Column("provider", sa.String(100)))
        batch.add_column(sa.Column("model", sa.String(255)))
        batch.add_column(sa.Column("processing_mode", sa.String(16), nullable=False, server_default="single"))
        batch.add_column(sa.Column("operation_item_id", sa.String(255)))
        batch.add_column(sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))

    with op.batch_alter_table("ai_usage_records") as batch:
        batch.add_column(sa.Column("processing_mode", sa.String(16), nullable=False, server_default="single"))
        batch.alter_column("locally_estimated_cost_micros", existing_type=sa.BigInteger(), nullable=True)

    with op.batch_alter_table("tenant_provider_policies") as batch:
        batch.add_column(sa.Column("single_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("batch_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("emergency_stop", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("single_active_jobs_limit", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("batch_active_jobs_limit", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("single_active_jobs", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("batch_active_jobs", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("daily_budget_limit_micros", sa.BigInteger()))
        batch.add_column(sa.Column("monthly_budget_limit_micros", sa.BigInteger()))
        batch.add_column(sa.Column("budget_currency", sa.String(3), nullable=False, server_default="USD"))
        batch.add_column(sa.Column("allowed_models_json", sa.JSON()))
        batch.create_check_constraint("ck_provider_policy_mode_limits", "single_active_jobs_limit > 0 AND batch_active_jobs_limit > 0")
        batch.create_check_constraint("ck_provider_policy_mode_active", "single_active_jobs >= 0 AND batch_active_jobs >= 0")
        batch.create_check_constraint("ck_provider_policy_budgets", "(daily_budget_limit_micros IS NULL OR daily_budget_limit_micros >= 0) AND (monthly_budget_limit_micros IS NULL OR monthly_budget_limit_micros >= 0)")

    op.create_table("ai_runtime_controls",
        sa.Column("control_key", sa.String(100), primary_key=True),
        sa.Column("stopped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text()),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ai_budget_overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["asset_ai_analyses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id","analysis_id",name="uq_ai_budget_override_analysis"))

def downgrade() -> None:
    op.drop_table("ai_budget_overrides")
    op.drop_table("ai_runtime_controls")
    with op.batch_alter_table("tenant_provider_policies") as batch:
        batch.drop_constraint("ck_provider_policy_budgets", type_="check")
        batch.drop_constraint("ck_provider_policy_mode_active", type_="check")
        batch.drop_constraint("ck_provider_policy_mode_limits", type_="check")
        for name in ("allowed_models_json","budget_currency","monthly_budget_limit_micros","daily_budget_limit_micros","batch_active_jobs","single_active_jobs","batch_active_jobs_limit","single_active_jobs_limit","emergency_stop","batch_enabled","single_enabled"):
            batch.drop_column(name)
    with op.batch_alter_table("ai_usage_records") as batch:
        batch.alter_column("locally_estimated_cost_micros", existing_type=sa.BigInteger(), nullable=False, server_default="0")
        batch.drop_column("processing_mode")
    with op.batch_alter_table("ai_budget_reservations") as batch:
        for name in ("attempt_number","operation_item_id","processing_mode","model","provider"):
            batch.drop_column(name)
    with op.batch_alter_table("ai_cost_rates") as batch:
        batch.drop_constraint("uq_ai_cost_rate_version", type_="unique")
        batch.drop_constraint("ck_ai_cost_rate_mode", type_="check")
        batch.drop_column("processing_mode")
        batch.create_unique_constraint("uq_ai_cost_rate_version", ["provider","model","effective_at"])
