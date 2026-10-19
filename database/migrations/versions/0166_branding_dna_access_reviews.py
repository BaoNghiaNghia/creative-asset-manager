"""Add append-only documentary access review history.

Revision ID: 0166_branding_dna_access_reviews
Revises: 0165_branding_dna_access_declarations
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0166_branding_dna_access_reviews"
down_revision = "0165_branding_dna_access_declarations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_shop_access_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("declaration_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["bc_shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["declaration_id"], ["bc_shop_access_declarations.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "decision IN ('needs_documentation', 'rejected', 'documentation_noted')",
            name="ck_bc_access_review_decision"
        ),
    )
    op.create_index("ix_bc_access_review_tenant_shop_date", "bc_shop_access_reviews",
                    ["tenant_id", "shop_id", "created_at"])
    op.create_index("ix_bc_access_review_declaration_date", "bc_shop_access_reviews",
                    ["declaration_id", "created_at"])


def downgrade():
    # Never erase audit evidence on a live system.
    bind = op.get_bind()
    url = bind.engine.url
    disposable = (
        url.database == "cam_integration"
        or (url.get_backend_name() == "sqlite"
            and (url.database or "").rsplit("/", 1)[-1] == "access-review-test.db")
    )
    if os.environ.get("CAM_ALLOW_ACCESS_REVIEW_DROP_FOR_TEST") != "1" or not disposable:
        raise RuntimeError("Shop documentary review history protected; downgrade denied")
    op.drop_index("ix_bc_access_review_declaration_date", table_name="bc_shop_access_reviews")
    op.drop_index("ix_bc_access_review_tenant_shop_date", table_name="bc_shop_access_reviews")
    op.drop_table("bc_shop_access_reviews")
