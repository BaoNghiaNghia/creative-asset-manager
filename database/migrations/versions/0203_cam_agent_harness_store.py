"""Dormant tenant-scoped Agent Harness definitions and evidence ledger.

Revision ID: 0203_cam_agent_harness_store
Revises: 0202_etsy_spy_later

No backfill, no data rewrite, no job changes and no active agent dispatcher.
"""
from alembic import op
import sqlalchemy as sa

revision = "0203_cam_agent_harness_store"
down_revision = "0202_etsy_spy_later"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_agent_definitions",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(96), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("allowed_tools_json", sa.JSON(), nullable=False),
        sa.Column("preferred_executor", sa.String(96), nullable=False),
        sa.Column("fallback_executors_json", sa.JSON(), nullable=False),
        sa.Column("max_concurrent", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("max_concurrent >= 1 AND max_concurrent <= 32", name="ck_cam_agent_max_concurrent"),
        sa.CheckConstraint("max_retries >= 0 AND max_retries <= 10", name="ck_cam_agent_max_retries"),
        sa.PrimaryKeyConstraint("tenant_id", "agent_id", "version"),
    )
    op.create_index(
        "ix_cam_agent_definitions_tenant_created", "cam_agent_definitions", ["tenant_id", "created_at"],
    )
    op.create_table(
        "cam_agent_experiences",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(96), nullable=False),
        sa.Column("agent_version", sa.String(20), nullable=False),
        sa.Column("scope_key", sa.String(160), nullable=False),
        sa.Column("rule_key", sa.String(160), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("source_event_ids_json", sa.JSON(), nullable=False),
        sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
        sa.Column("verification_refs_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("evaluation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "agent_id", "agent_version"],
            ["cam_agent_definitions.tenant_id", "cam_agent_definitions.agent_id", "cam_agent_definitions.version"],
            name="fk_cam_experience_definition",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_cam_experience_confidence"),
        sa.CheckConstraint("sample_count >= 0", name="ck_cam_experience_sample_count"),
        sa.CheckConstraint(
            "status IN ('candidate', 'promoted', 'rejected')",
            name="ck_cam_experience_status",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "event_key"),
    )
    op.create_index(
        "ix_cam_experiences_eligible", "cam_agent_experiences",
        ["tenant_id", "agent_id", "agent_version", "scope_key", "status", "created_at"],
    )


def downgrade():
    # Destructive if the new tables contain data: production downgrade needs a
    # reviewed export/restore plan and must NEVER be automatic.
    op.drop_index("ix_cam_experiences_eligible", table_name="cam_agent_experiences")
    op.drop_table("cam_agent_experiences")
    op.drop_index("ix_cam_agent_definitions_tenant_created", table_name="cam_agent_definitions")
    op.drop_table("cam_agent_definitions")
