"""Add Inventory Phase 6 review audit state.

Revision ID: 0037_inventory_normalize_review
Revises: 0036_inventory_ai
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_inventory_normalize_review"
down_revision = "0036_inventory_ai"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("inventory_documents") as batch_op:
        batch_op.drop_constraint("ck_inventory_documents_status", type_="check")
        batch_op.create_check_constraint("ck_inventory_documents_status", "status IN ('collecting','preparing','prepared','duplicate','retryable_failure','terminal_failure','analyzing','validating','needs_review','needs_reupload','approved','rejected','finalized')")
    op.create_table("inventory_review_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(255)),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "review_id"], ["inventory_reviews.tenant_id", "inventory_reviews.id"], ondelete="CASCADE", name="fk_inventory_review_events_tenant_review"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inventory_review_events_tenant_id"),
    )
    op.create_index("ix_inventory_review_events_review", "inventory_review_events", ["tenant_id", "review_id", "created_at"])

def downgrade():
    op.drop_index("ix_inventory_review_events_review", table_name="inventory_review_events")
    op.drop_table("inventory_review_events")
    # Phase 5 has no validating/needs_reupload states. Preserve safe
    # semantics before reinstating its historical constraint on rollback.
    op.execute("UPDATE inventory_documents SET status = 'analyzing' WHERE status = 'validating'")
    op.execute("UPDATE inventory_documents SET status = 'needs_review' WHERE status = 'needs_reupload'")
    with op.batch_alter_table("inventory_documents") as batch_op:
        batch_op.drop_constraint("ck_inventory_documents_status", type_="check")
        batch_op.create_check_constraint("ck_inventory_documents_status", "status IN ('collecting','preparing','prepared','duplicate','retryable_failure','terminal_failure','analyzing','needs_review','approved','rejected','finalized')")
