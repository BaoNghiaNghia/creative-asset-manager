"""Add single-asset AI analysis execution audit fields.

Revision ID: 0008_ai_single_analysis
Revises: 0007_search_operations

Rollback: stop AI workers and drain asset_analyze jobs before downgrade. Completed
metadata documents remain intact; only execution audit and claim fields are removed.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_ai_single_analysis"
down_revision = "0007_search_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset_ai_analyses", sa.Column("processing_stage", sa.String(40)))
    op.add_column("asset_ai_analyses", sa.Column("claimed_by", sa.String(255)))
    op.add_column(
        "asset_ai_analyses",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column("asset_ai_analyses", sa.Column("projection_checksum", sa.String(64)))
    op.add_column("asset_ai_analyses", sa.Column("provider_request_id", sa.String(255)))
    op.add_column("asset_ai_analyses", sa.Column("usage_json", sa.JSON()))
    op.add_column("asset_ai_analyses", sa.Column("provider_metadata_json", sa.JSON()))
    op.add_column("asset_ai_analyses", sa.Column("validation_errors_json", sa.JSON()))
    op.add_column("asset_ai_analyses", sa.Column("failure_retryable", sa.Boolean()))
    op.create_index(
        "ix_asset_ai_analyses_claim",
        "asset_ai_analyses",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_asset_ai_analyses_claim", table_name="asset_ai_analyses")
    op.drop_column("asset_ai_analyses", "failure_retryable")
    op.drop_column("asset_ai_analyses", "validation_errors_json")
    op.drop_column("asset_ai_analyses", "provider_metadata_json")
    op.drop_column("asset_ai_analyses", "usage_json")
    op.drop_column("asset_ai_analyses", "provider_request_id")
    op.drop_column("asset_ai_analyses", "projection_checksum")
    op.drop_column("asset_ai_analyses", "lease_expires_at")
    op.drop_column("asset_ai_analyses", "claimed_by")
    op.drop_column("asset_ai_analyses", "processing_stage")
