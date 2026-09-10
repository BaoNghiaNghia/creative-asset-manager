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
    with op.batch_alter_table("tenant_processing_policies") as batch_op:
        batch_op.add_column(
            sa.Column(
                "job_priorities_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.alter_column(
            "job_priorities_json",
            existing_type=sa.JSON(),
            server_default=None,
        )


def downgrade():
    with op.batch_alter_table("tenant_processing_policies") as batch_op:
        batch_op.drop_column("job_priorities_json")
