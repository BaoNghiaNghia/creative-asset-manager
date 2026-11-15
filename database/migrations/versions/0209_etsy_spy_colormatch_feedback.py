"""ColorMatch V7: additive tenant-scoped explicit user preference events.

Revision ID: 0209_etsy_spy_colormatch_feedback
Revises: 0208_etsy_spy_agent_review_receipts
"""
from alembic import op
import sqlalchemy as sa

revision = "0209_etsy_spy_colormatch_feedback"
down_revision = "0208_etsy_spy_agent_review_receipts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "etsy_spy_color_feedback",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("asset_id", sa.String(24), nullable=False),
        sa.Column("output_sha256", sa.String(64), nullable=False),
        sa.Column("color_id", sa.String(40), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("scope", sa.String(12), nullable=False),
        sa.Column("print_method", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("action IN ('selected','rejected')",
                           name="ck_etsy_spy_color_feedback_action"),
        sa.CheckConstraint("scope IN ('design','product')",
                           name="ck_etsy_spy_color_feedback_scope"),
        sa.CheckConstraint("print_method IN ('unknown','dtg','dtf','screen')",
                           name="ck_etsy_spy_color_feedback_print_method"),
    )
    op.create_index(
        "ix_etsy_spy_color_feedback_sha",
        "etsy_spy_color_feedback",
        ["tenant_id", "output_sha256", "created_at"],
    )
    op.create_index(
        "ix_etsy_spy_color_feedback_listing",
        "etsy_spy_color_feedback", ["tenant_id", "listing_key"],
    )


def downgrade():
    # Preferences are user-entered evidence. Preserve them during normal
    # code-only rollback; an empty table can be dropped by an administrator.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM etsy_spy_color_feedback")):
        raise RuntimeError("Cannot downgrade populated ColorMatch feedback evidence")
    op.drop_index("ix_etsy_spy_color_feedback_listing", table_name="etsy_spy_color_feedback")
    op.drop_index("ix_etsy_spy_color_feedback_sha", table_name="etsy_spy_color_feedback")
    op.drop_table("etsy_spy_color_feedback")
