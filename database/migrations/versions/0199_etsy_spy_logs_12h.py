"""Prune Etsy Spy System Logs older than the latest twelve hours.

Revision ID: 0199_etsy_spy_logs_12h
Revises: 0198_etsy_spy_picked
"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0199_etsy_spy_logs_12h"
down_revision = "0198_etsy_spy_picked"
branch_labels = None
depends_on = None


def upgrade():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    op.get_bind().execute(
        sa.text("DELETE FROM etsy_spy_system_logs WHERE event_at < :cutoff"),
        {"cutoff": cutoff},
    )


def downgrade():
    # Time-expired messages cannot be reconstructed on rollback.
    pass
