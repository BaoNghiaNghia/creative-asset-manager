"""Add durable AI analysis orchestration requests.

Revision ID: 0020_ai_analysis_requests
Revises: 0019_ai_provider_selection

Rollback: stop API writes and batch preparation workers, retain/export request
audit data if required, then downgrade. Analyses, provider batches and processing
jobs remain authoritative and are not removed.
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_ai_analysis_requests"
down_revision = "0019_ai_provider_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("metadata_profile_id", sa.String(36), nullable=False),
        sa.Column("metadata_profile", sa.String(255), nullable=False),
        sa.Column("metadata_profile_version", sa.String(100)),
        sa.Column("ai_provider", sa.String(100), nullable=False),
        sa.Column("ai_model", sa.String(255), nullable=False),
        sa.Column("processing_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("warning", sa.String(255)),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("cancellation_reason", sa.Text()),
        sa.Column("cancelled_by", sa.String(255)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metadata_profile_id"],
            ["metadata_profiles.tenant_id", "metadata_profiles.id"],
            ondelete="RESTRICT",
            name="fk_ai_analysis_requests_tenant_profile",
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key",
            name="uq_ai_analysis_requests_tenant_key",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id",
            name="uq_ai_analysis_requests_tenant_id",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'cancelled')",
            name="ck_ai_analysis_requests_status",
        ),
        sa.CheckConstraint(
            "processing_mode IN ('single', 'batch')",
            name="ck_ai_analysis_requests_mode",
        ),
        sa.CheckConstraint(
            "item_count > 0",
            name="ck_ai_analysis_requests_item_count",
        ),
    )
    op.create_index(
        "ix_ai_analysis_requests_tenant_created",
        "ai_analysis_requests",
        ["tenant_id", "created_at"],
    )
    op.create_table(
        "ai_analysis_request_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("requested_asset_id", sa.String(36), nullable=False),
        sa.Column("analysis_id", sa.String(36)),
        sa.Column("processing_job_id", sa.String(36)),
        sa.Column("acceptance_status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["ai_analysis_requests.tenant_id", "ai_analysis_requests.id"],
            ondelete="CASCADE",
            name="fk_ai_analysis_request_items_tenant_request",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["asset_ai_analyses.id"],
            ondelete="RESTRICT",
            name="fk_ai_analysis_request_items_analysis",
        ),
        sa.ForeignKeyConstraint(
            ["processing_job_id"], ["processing_jobs.id"],
            ondelete="SET NULL",
            name="fk_ai_analysis_request_items_job",
        ),
        sa.UniqueConstraint(
            "request_id", "requested_asset_id",
            name="uq_ai_analysis_request_items_asset",
        ),
        sa.CheckConstraint(
            "acceptance_status IN "
            "('accepted', 'already_exists', 'invalid_asset', 'unauthorized', "
            "'provider_unavailable', 'budget_preflight_failed')",
            name="ck_ai_analysis_request_items_acceptance",
        ),
    )
    op.create_index(
        "ix_ai_analysis_request_items_request",
        "ai_analysis_request_items",
        ["request_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_analysis_request_items_request",
        table_name="ai_analysis_request_items",
    )
    op.drop_table("ai_analysis_request_items")
    op.drop_index(
        "ix_ai_analysis_requests_tenant_created",
        table_name="ai_analysis_requests",
    )
    op.drop_table("ai_analysis_requests")
