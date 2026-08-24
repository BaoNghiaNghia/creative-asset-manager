"""Add tenant-scoped daily Google Sheet inventory automation.

Revision ID: 0050_inventory_daily_sheets
Revises: 0049_video_analysis_persistence
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0050_inventory_daily_sheets"
down_revision = "0049_video_analysis_persistence"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("inventory_settings", sa.Column("image_pipeline_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("inventory_settings", sa.Column("daily_sheet_automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    for name in (
        "daily_working_spreadsheet_file_id", "daily_archive_root_folder_id",
        "daily_template_spreadsheet_file_id", "daily_target_spreadsheet_file_id",
    ):
        op.add_column("inventory_settings", sa.Column(name, sa.String(length=2048), nullable=True))
    op.add_column("inventory_settings", sa.Column("daily_snapshot_time_local", sa.String(length=5), nullable=False, server_default="05:50"))
    op.add_column("inventory_settings", sa.Column("daily_reconcile_time_local", sa.String(length=5), nullable=False, server_default="07:00"))
    op.add_column("inventory_settings", sa.Column("daily_sheet_config_json", JSON_DOCUMENT, nullable=False, server_default=sa.text("'{}'")))

    op.create_table(
        "inventory_daily_sheet_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("external_source_id", sa.String(length=36), nullable=False),
        sa.Column("source_spreadsheet_file_id", sa.String(length=2048), nullable=False),
        sa.Column("source_modified_time_before", sa.DateTime(timezone=True)),
        sa.Column("source_modified_time_after", sa.DateTime(timezone=True)),
        sa.Column("source_data_hash", sa.String(length=64)),
        sa.Column("archive_folder_id", sa.String(length=2048)),
        sa.Column("snapshot_file_id", sa.String(length=2048)),
        sa.Column("snapshot_data_hash", sa.String(length=64)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cloned_at", sa.DateTime(timezone=True)),
        sa.Column("reset_started_at", sa.DateTime(timezone=True)),
        sa.Column("reset_completed_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "external_source_id"], ["external_sources.tenant_id", "external_sources.id"], ondelete="RESTRICT", name="fk_inventory_sheet_snapshot_tenant_source"),
        sa.UniqueConstraint("tenant_id", "business_date", name="uq_inventory_sheet_snapshot_tenant_date"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inventory_sheet_snapshot_tenant_id"),
        sa.CheckConstraint("status IN ('pending','cloning','cloned','resetting','completed','retryable_failure','terminal_failure')", name="ck_inventory_sheet_snapshot_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_inventory_sheet_snapshot_attempt_count"),
    )
    op.create_index("ix_inventory_sheet_snapshot_status", "inventory_daily_sheet_snapshots", ["tenant_id", "status", "business_date"])

    op.create_table(
        "inventory_daily_sheet_reconciliations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("current_snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(length=36)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_hash", sa.String(length=64)),
        sa.Column("target_before_hash", sa.String(length=64)),
        sa.Column("target_after_hash", sa.String(length=64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("summary_json", JSON_DOCUMENT, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id", "current_snapshot_id"], ["inventory_daily_sheet_snapshots.tenant_id", "inventory_daily_sheet_snapshots.id"], ondelete="RESTRICT", name="fk_inventory_sheet_reconcile_current"),
        sa.ForeignKeyConstraint(["tenant_id", "previous_snapshot_id"], ["inventory_daily_sheet_snapshots.tenant_id", "inventory_daily_sheet_snapshots.id"], ondelete="RESTRICT", name="fk_inventory_sheet_reconcile_previous"),
        sa.UniqueConstraint("tenant_id", "business_date", name="uq_inventory_sheet_reconcile_tenant_date"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inventory_sheet_reconcile_tenant_id"),
        sa.CheckConstraint("status IN ('pending','planning','writing','completed','awaiting_baseline','baseline','retryable_failure','terminal_failure')", name="ck_inventory_sheet_reconcile_status"),
    )
    op.create_index("ix_inventory_sheet_reconcile_status", "inventory_daily_sheet_reconciliations", ["tenant_id", "status", "business_date"])

    # SQLite cannot drop column defaults without rebuilding the table. Keeping
    # these development-only defaults is harmless; PostgreSQL production drops them.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("inventory_settings", "image_pipeline_enabled", server_default=None)
        op.alter_column("inventory_settings", "daily_sheet_automation_enabled", server_default=None)
        op.alter_column("inventory_settings", "daily_snapshot_time_local", server_default=None)
        op.alter_column("inventory_settings", "daily_reconcile_time_local", server_default=None)
        op.alter_column("inventory_settings", "daily_sheet_config_json", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_inventory_sheet_reconcile_status", table_name="inventory_daily_sheet_reconciliations")
    op.drop_table("inventory_daily_sheet_reconciliations")
    op.drop_index("ix_inventory_sheet_snapshot_status", table_name="inventory_daily_sheet_snapshots")
    op.drop_table("inventory_daily_sheet_snapshots")
    for name in (
        "daily_sheet_config_json", "daily_reconcile_time_local", "daily_snapshot_time_local",
        "daily_target_spreadsheet_file_id", "daily_template_spreadsheet_file_id",
        "daily_archive_root_folder_id", "daily_working_spreadsheet_file_id",
        "daily_sheet_automation_enabled", "image_pipeline_enabled",
    ):
        op.drop_column("inventory_settings", name)
