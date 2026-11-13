"""Agent-origin Etsy Spy manual decisions with durable PostgreSQL receipts.

Revision ID: 0208_etsy_spy_agent_review_receipts
Revises: 0207_cam_agent_trial_artwork_snapshots
"""
from alembic import op
import sqlalchemy as sa

revision = "0208_etsy_spy_agent_review_receipts"
down_revision = "0207_cam_agent_trial_artwork_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("etsy_spy_listings", sa.Column(
        "cart_lower_bound", sa.Boolean(), nullable=True))
    op.add_column("etsy_spy_listings", sa.Column(
        "item_reviews", sa.Integer(), nullable=True))
    op.create_table(
        "etsy_spy_review_receipts",
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("keyword", sa.String(220), nullable=False),
        sa.Column("evidence_hash", sa.String(32), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "agent_id", "request_id"),
        sa.CheckConstraint("action IN ('pick','later','win_keyword')",
                           name="ck_etsy_spy_review_receipt_action"),
        sa.CheckConstraint("outcome IN ('created','already')",
                           name="ck_etsy_spy_review_receipt_outcome"),
    )
    op.create_index("ix_etsy_spy_review_receipts_listing",
                    "etsy_spy_review_receipts", ["tenant_id", "listing_key"])


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM etsy_spy_review_receipts")):
        raise RuntimeError(
            "Review receipts are permanent audit/idempotency evidence. "
            "Code-only rollback is safe; populated table downgrade denied."
        )
    op.drop_index("ix_etsy_spy_review_receipts_listing",
                  table_name="etsy_spy_review_receipts")
    if connection.scalar(sa.text(
        "SELECT COUNT(*) FROM etsy_spy_listings "
        "WHERE item_reviews IS NOT NULL OR cart_lower_bound = TRUE"
    )):
        raise RuntimeError(
            "Refusing loss of source-verified Shop review evidence during downgrade"
        )
    op.drop_table("etsy_spy_review_receipts")
    op.drop_column("etsy_spy_listings", "item_reviews")
    op.drop_column("etsy_spy_listings", "cart_lower_bound")
