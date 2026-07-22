"""Add bounded AI Operations dashboard indexes.

Revision ID: 0022_ai_operations_indexes
Revises: 0021_ai_multi_governance

Rollback: downgrade to 0021. Only reporting indexes are removed; no data is
changed or deleted.
"""
from alembic import op


revision = "0022_ai_operations_indexes"
down_revision = "0021_ai_multi_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_asset_ai_analyses_tenant_created", "asset_ai_analyses", ["tenant_id", "created_at"])
    op.create_index("ix_asset_ai_analyses_tenant_status_created", "asset_ai_analyses", ["tenant_id", "status", "created_at"])
    op.create_index("ix_ai_batch_jobs_tenant_created", "ai_batch_jobs", ["tenant_id", "created_at"])
    op.create_index("ix_ai_batch_jobs_tenant_provider_status_created", "ai_batch_jobs", ["tenant_id", "provider", "status", "created_at"])
    op.create_index("ix_processing_jobs_tenant_created", "processing_jobs", ["tenant_id", "created_at"])
    op.create_index("ix_processing_jobs_tenant_status_created", "processing_jobs", ["tenant_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_tenant_status_created", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_tenant_created", table_name="processing_jobs")
    op.drop_index("ix_ai_batch_jobs_tenant_provider_status_created", table_name="ai_batch_jobs")
    op.drop_index("ix_ai_batch_jobs_tenant_created", table_name="ai_batch_jobs")
    op.drop_index("ix_asset_ai_analyses_tenant_status_created", table_name="asset_ai_analyses")
    op.drop_index("ix_asset_ai_analyses_tenant_created", table_name="asset_ai_analyses")
