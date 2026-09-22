"""Add Inventory operation audit and knowledge base.

Revision ID: 0087_inventory_audit_knowledge
Revises: 0086_source_asset_parent_listing
"""
from alembic import op
import sqlalchemy as sa

revision = "0087_inventory_audit_knowledge"
down_revision = "0086_source_asset_parent_listing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_inventory_jobs_tenant_type_entity",
        "inventory_jobs",
        ["tenant_id", "job_type", "entity_id"],
    )
    op.add_column(
        "inventory_daily_carry_forwards",
        sa.Column("knowledge_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "inventory_daily_carry_forwards",
        sa.Column("knowledge_version", sa.Integer(), nullable=True),
    )

    op.create_table(
        "inventory_operation_audits",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("assessment_json", sa.JSON(), nullable=False),
        sa.Column("tool_trace_json", sa.JSON(), nullable=False),
        sa.Column("read_ranges_json", sa.JSON(), nullable=False),
        sa.Column("prompt_source", sa.String(length=32), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("knowledge_hash", sa.String(length=64), nullable=True),
        sa.Column("knowledge_version", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("writes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stage IN ('morning_reset','afternoon_snapshot','evening_reconcile','manual_prompt_test')",
            name="ck_inventory_operation_audits_stage",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','shadow','completed','review_required','blocked','failed')",
            name="ck_inventory_operation_audits_status",
        ),
        sa.UniqueConstraint("tenant_id", "business_date", "stage", "run_id", name="uq_inventory_operation_audit_run"),
    )
    op.create_index(
        "ix_inventory_operation_audits_tenant_date",
        "inventory_operation_audits",
        ["tenant_id", "business_date", "stage", "created_at"],
    )

    op.create_table(
        "inventory_operation_changes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audit_id", sa.String(length=36), sa.ForeignKey("inventory_operation_audits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sheet", sa.String(length=255), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("cell", sa.String(length=32), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=False),
        sa.Column("after_json", sa.JSON(), nullable=False),
        sa.Column("source_sheet", sa.String(length=255), nullable=True),
        sa.Column("source_cell", sa.String(length=32), nullable=True),
        sa.Column("material_id", sa.String(length=255), nullable=True),
        sa.Column("warehouse_id", sa.String(length=255), nullable=True),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('set_cell','clear_cell')",
            name="ck_inventory_operation_changes_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ('verified','not_executed','failed','unknown')",
            name="ck_inventory_operation_changes_verification",
        ),
        sa.UniqueConstraint("audit_id", "sequence", name="uq_inventory_operation_change_sequence"),
    )
    op.create_index(
        "ix_inventory_operation_changes_audit",
        "inventory_operation_changes",
        ["audit_id", "sequence"],
    )
    op.create_index(
        "ix_inventory_operation_changes_tenant_row",
        "inventory_operation_changes",
        ["tenant_id", "sheet", "row_number"],
    )

    op.create_table(
        "inventory_knowledge_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_rule_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_run_id", sa.String(length=128), nullable=True),
        sa.Column("source_content_hash", sa.String(length=64), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.String(length=36),
            sa.ForeignKey("inventory_knowledge_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("activated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version > 0", name="ck_inventory_knowledge_version_positive"),
        sa.CheckConstraint(
            "status IN ('proposed','draft','active','archived','rejected')",
            name="ck_inventory_knowledge_status",
        ),
        sa.CheckConstraint(
            "kind IN ('RULE','EXCEPTION','COLUMN_MEANING','ROW_TYPE','FORMULA','MATERIAL_MAPPING','WAREHOUSE_MAPPING','UNIT_CONVERSION','NAMING_PATTERN','DO_NOT_EDIT','BUSINESS_NOTE')",
            name="ck_inventory_knowledge_kind",
        ),
        sa.UniqueConstraint("tenant_id", "knowledge_key", "version", name="uq_inventory_knowledge_version"),
        sa.UniqueConstraint(
            "tenant_id", "source_run_id", "source_content_hash",
            name="uq_inventory_knowledge_proposal_source",
        ),
    )
    op.create_index(
        "ix_inventory_knowledge_tenant_status",
        "inventory_knowledge_entries",
        ["tenant_id", "status", "kind", "updated_at"],
    )
    op.create_index(
        "ix_inventory_knowledge_source_run",
        "inventory_knowledge_entries",
        ["tenant_id", "source_run_id", "source_content_hash"],
    )
    op.create_index(
        "uq_inventory_knowledge_active_key",
        "inventory_knowledge_entries",
        ["tenant_id", "knowledge_key"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_inventory_knowledge_active_key", table_name="inventory_knowledge_entries")
    op.drop_index("ix_inventory_knowledge_source_run", table_name="inventory_knowledge_entries")
    op.drop_index("ix_inventory_knowledge_tenant_status", table_name="inventory_knowledge_entries")
    op.drop_table("inventory_knowledge_entries")
    op.drop_index("ix_inventory_operation_changes_tenant_row", table_name="inventory_operation_changes")
    op.drop_index("ix_inventory_operation_changes_audit", table_name="inventory_operation_changes")
    op.drop_table("inventory_operation_changes")
    op.drop_index("ix_inventory_operation_audits_tenant_date", table_name="inventory_operation_audits")
    op.drop_table("inventory_operation_audits")
    op.drop_column("inventory_daily_carry_forwards", "knowledge_version")
    op.drop_column("inventory_daily_carry_forwards", "knowledge_hash")
    op.drop_index(
        "ix_inventory_jobs_tenant_type_entity",
        table_name="inventory_jobs",
    )
