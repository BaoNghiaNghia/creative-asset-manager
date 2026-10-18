"""Add review-only shop authorization dossiers (no scan enablement).

Revision ID: 0166_bdna_scan_authorization_reviews
Revises: 0165_bdna_scan_failure_receipts
"""
import os
import sqlalchemy as sa
from alembic import op

revision = "0166_bdna_scan_authorization_reviews"
down_revision = "0165_bdna_scan_failure_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_scan_authorization_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("storefront_sha256", sa.String(64), nullable=False),
        sa.Column("policy_scope", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("submitted_by", sa.String(255), nullable=False),
        sa.Column("reviewed_by", sa.String(255)),
        sa.Column("review_note", sa.String(500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(255)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_note", sa.String(500), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "document_id"],
            ["bc_knowledge_documents.tenant_id", "bc_knowledge_documents.shop_id",
             "bc_knowledge_documents.id"],
            name="fk_bc_scan_review_policy_doc_scope", ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "shop_id", "document_id",
                            name="uq_bc_scan_review_source_doc"),
        sa.CheckConstraint("status IN ('pending_review','human_reviewed','rejected','revoked')",
                           name="ck_bc_scan_review_status"),
        sa.CheckConstraint("policy_scope IN ('etsy_written_access','amazon_agent_policy')",
                           name="ck_bc_scan_review_scope"),
    )
    op.create_index("ix_bc_scan_review_shop_created", "bc_scan_authorization_reviews",
                    ["tenant_id", "shop_id", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    url = bind.engine.url
    if not (os.environ.get("CAM_ALLOW_SCAN_REVIEW_DATA_DROP_FOR_TEST") == "1"
            and (url.database == "cam_integration" or
                 url.get_backend_name() == "sqlite" and
                 (url.database or "").rsplit("/", 1)[-1] == "scan-review-test.db")):
        raise RuntimeError("Marketplace scan review history protected; downgrade denied")
    op.drop_index("ix_bc_scan_review_shop_created", table_name="bc_scan_authorization_reviews")
    op.drop_table("bc_scan_authorization_reviews")
