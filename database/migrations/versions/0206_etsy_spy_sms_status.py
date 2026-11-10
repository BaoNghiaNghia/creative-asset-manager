"""Add tenant-scoped SMS delivery receipt projection (read-only for CAM clients).

Revision ID: 0206_etsy_spy_sms_status
Revises: 0205_cam_agent_trial_evidence
"""
from alembic import op
import sqlalchemy as sa

revision = "0206_etsy_spy_sms_status"
down_revision = "0205_cam_agent_trial_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_sms_statuses",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("sms_code", sa.String(80), nullable=True),
        sa.Column("sms_id", sa.String(80), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "listing_key"),
    )


def downgrade():
    # This table contains sent receipts; back up the projection before downgrade.
    op.drop_table("etsy_spy_sms_statuses")
