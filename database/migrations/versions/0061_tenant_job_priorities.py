"""add tenant job priority settings

Revision ID: 0061_tenant_job_priorities
Revises: 0060_onedrive_authorities
"""
from alembic import op
import sqlalchemy as sa


revision = "0061_tenant_job_priorities"
down_revision = "0060_onedrive_authorities"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenant_processing_policies",
        sa.Column("job_priorities_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("tenant_processing_policies", "job_priorities_json", server_default=None)


def downgrade():
    op.drop_column("tenant_processing_policies", "job_priorities_json")
