"""Add isolated Inventory AI extraction state.

Revision ID: 0036_inventory_ai
Revises: 0035_inventory_document_prep
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_inventory_ai"
down_revision = "0035_inventory_document_prep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_ai_controls",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("emergency_stop", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("provider", sa.String(64), server_default="gemini", nullable=False),
        sa.Column("allowed_models_json", sa.JSON(), nullable=False),
        sa.Column("max_concurrent", sa.Integer(), server_default="1", nullable=False),
        sa.Column("min_start_interval_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("per_run_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("daily_budget_micros", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("monthly_budget_micros", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_inventory_ai_controls_tenant"),
        sa.CheckConstraint("max_concurrent > 0", name="ck_inventory_ai_controls_concurrent"),
        sa.CheckConstraint("min_start_interval_seconds >= 0", name="ck_inventory_ai_controls_interval"),
        sa.CheckConstraint("per_run_limit > 0", name="ck_inventory_ai_controls_per_run"),
        sa.CheckConstraint("daily_budget_micros >= 0", name="ck_inventory_ai_controls_daily_budget"),
        sa.CheckConstraint("monthly_budget_micros >= 0", name="ck_inventory_ai_controls_monthly_budget"),
    )
    with op.batch_alter_table("inventory_ai_analyses") as batch:
        batch.drop_constraint("ck_inventory_ai_status", type_="check")
        batch.add_column(sa.Column("content_sha256", sa.String(64)))
        batch.add_column(sa.Column("extraction_profile", sa.String(128), server_default="inventory-stock-sheet", nullable=False))
        batch.add_column(sa.Column("extraction_profile_version", sa.String(64), server_default="v1", nullable=False))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("provider_request_id", sa.String(255)))
        batch.add_column(sa.Column("usage_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("extracted_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint("ck_inventory_ai_status", "status IN ('pending','processing','completed','failed','queued','analyzing','succeeded','retryable_failure','terminal_failure','superseded')")


def downgrade() -> None:
    with op.batch_alter_table("inventory_ai_analyses") as batch:
        batch.drop_constraint("ck_inventory_ai_status", type_="check")
        for name in ("started_at", "extracted_json", "estimated_cost_micros", "usage_json", "provider_request_id", "attempt_count", "extraction_profile_version", "extraction_profile", "content_sha256"):
            batch.drop_column(name)
        batch.create_check_constraint("ck_inventory_ai_status", "status IN ('pending','processing','completed','failed','superseded')")
    op.drop_table("inventory_ai_controls")
