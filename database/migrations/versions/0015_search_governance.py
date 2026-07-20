"""Deterministic analysis selection, search shadow observations and index lifecycle.

Revision ID: 0015_search_governance
Revises: 0014_reconciliation_retention

Rollback removes Step 33 operational state. Stop shadow comparisons and index
lifecycle operations before downgrade; analysis history and Elasticsearch data
are not deleted by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_search_governance"
down_revision = "0014_reconciliation_retention"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "active_asset_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("metadata_profile_id", sa.String(36), nullable=False),
        sa.Column("search_context", sa.String(64), nullable=False),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("activated_by", sa.String(255), nullable=False),
        sa.Column("activation_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], ondelete="CASCADE", name="fk_active_analysis_tenant_asset"),
        sa.ForeignKeyConstraint(["analysis_id"], ["asset_ai_analyses.id"], ondelete="RESTRICT", name="fk_active_analysis_analysis"),
        sa.UniqueConstraint("tenant_id", "asset_id", "metadata_profile_id", "search_context", name="uq_active_asset_analysis_context"),
    )
    op.create_index("ix_active_asset_analyses_analysis", "active_asset_analyses", ["tenant_id", "analysis_id"])
    op.create_table(
        "active_analysis_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("metadata_profile_id", sa.String(36), nullable=False),
        sa.Column("search_context", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("previous_analysis_id", sa.String(36)),
        sa.Column("analysis_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_active_analysis_audits_tenant_asset", "active_analysis_audits", ["tenant_id", "asset_id", "created_at"])

    op.create_table(
        "tenant_search_shadow_policies",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("emergency_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("primary_version", sa.String(8), nullable=False, server_default="v1"),
        sa.Column("shadow_version", sa.String(8), nullable=False, server_default="v2"),
        sa.Column("sample_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="250"),
        sa.Column("persist_raw_query", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw_query_retention_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("report_retention_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("top_k", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("primary_version IN ('v1','v2')", name="ck_shadow_primary_version"),
        sa.CheckConstraint("shadow_version IN ('v1','v2')", name="ck_shadow_secondary_version"),
        sa.CheckConstraint("primary_version <> shadow_version", name="ck_shadow_distinct_versions"),
        sa.CheckConstraint("sample_percentage >= 0 AND sample_percentage <= 100", name="ck_shadow_sample_percentage"),
        sa.CheckConstraint("timeout_ms > 0 AND timeout_ms <= 10000", name="ck_shadow_timeout"),
    )
    op.create_table(
        "search_shadow_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("raw_query", sa.Text()),
        sa.Column("query_type", sa.String(32), nullable=False),
        sa.Column("query_features_json", sa.JSON(), nullable=False),
        sa.Column("metadata_profile", sa.String(255)),
        sa.Column("primary_version", sa.String(8), nullable=False),
        sa.Column("shadow_version", sa.String(8), nullable=False),
        sa.Column("primary_latency_ms", sa.Integer(), nullable=False),
        sa.Column("shadow_latency_ms", sa.Integer()),
        sa.Column("primary_count", sa.Integer(), nullable=False),
        sa.Column("shadow_count", sa.Integer()),
        sa.Column("top_k_overlap", sa.Float()),
        sa.Column("rank_correlation", sa.Float()),
        sa.Column("top_result_agrees", sa.Boolean()),
        sa.Column("zero_result_disagrees", sa.Boolean()),
        sa.Column("error_category", sa.String(64)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shadow_observations_report", "search_shadow_observations", ["tenant_id", "occurred_at", "query_type"])

    op.create_table(
        "search_index_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("physical_index_name", sa.String(255), nullable=False),
        sa.Column("index_prefix", sa.String(128), nullable=False),
        sa.Column("index_version", sa.String(128), nullable=False),
        sa.Column("projection_version", sa.String(100), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexing_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verification_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("physical_index_name", name="uq_search_index_record_name"),
        sa.CheckConstraint("lifecycle_state IN ('building','validating','active','previous','retired','deletion_pending','deleted','failed')", name="ck_search_index_lifecycle_state"),
    )
    op.create_index("ix_search_index_lifecycle", "search_index_records", ["index_prefix", "lifecycle_state", "created_at"])
    op.create_table(
        "search_index_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("index_record_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("old_state", sa.String(32)),
        sa.Column("new_state", sa.String(32)),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["index_record_id"], ["search_index_records.id"], ondelete="RESTRICT", name="fk_search_index_audit_record"),
    )
    op.create_index("ix_search_index_audits_created", "search_index_audits", ["index_record_id", "created_at"])


def downgrade():
    op.drop_index("ix_search_index_audits_created", table_name="search_index_audits")
    op.drop_table("search_index_audits")
    op.drop_index("ix_search_index_lifecycle", table_name="search_index_records")
    op.drop_table("search_index_records")
    op.drop_index("ix_shadow_observations_report", table_name="search_shadow_observations")
    op.drop_table("search_shadow_observations")
    op.drop_table("tenant_search_shadow_policies")
    op.drop_index("ix_active_analysis_audits_tenant_asset", table_name="active_analysis_audits")
    op.drop_table("active_analysis_audits")
    op.drop_index("ix_active_asset_analyses_analysis", table_name="active_asset_analyses")
    op.drop_table("active_asset_analyses")
