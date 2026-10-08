"""Stage 0 trademark screening evidence, independent of Google Ads volume.

Revision ID: 0140_rrugc_keyword_trademark
Revises: 0139_rrugc_scout_feedback

Existing keywords are deliberately backfilled as UNVERIFIED. A keyword is not
cleared merely because the AEBrowse provider returned search volume.
"""
from alembic import op
import sqlalchemy as sa


revision = "0140_rrugc_keyword_trademark"
down_revision = "0139_rrugc_scout_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column(
            "trademark_status", sa.String(length=24),
            server_default="unverified", nullable=False,
        ),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("trademark_checked_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("trademark_source", sa.String(length=64)),
    )
    op.add_column(
        "rrugc_keyword_volumes",
        sa.Column("trademark_match_count", sa.Integer()),
    )
    op.create_index(
        "ix_rrugc_keyword_volume_trademark",
        "rrugc_keyword_volumes", ["tenant_id", "trademark_status"],
    )
    # Keep database default for future Scout-ingested keywords as well.
    # Every preexisting row is backfilled atomically by ADD COLUMN.


def downgrade() -> None:
    op.drop_index("ix_rrugc_keyword_volume_trademark", table_name="rrugc_keyword_volumes")
    op.drop_column("rrugc_keyword_volumes", "trademark_match_count")
    op.drop_column("rrugc_keyword_volumes", "trademark_source")
    op.drop_column("rrugc_keyword_volumes", "trademark_checked_at")
    op.drop_column("rrugc_keyword_volumes", "trademark_status")
