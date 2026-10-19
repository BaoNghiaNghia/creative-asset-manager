"""Record operator shop relationship declarations without granting scan permissions.

Revision ID: 0165_branding_dna_access_declarations
Revises: 0164_branding_dna_shop_rag
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0165_branding_dna_access_declarations"
down_revision = "0164_branding_dna_shop_rag"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_shop_access_declarations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("relationship", sa.String(24), nullable=False),
        sa.Column("evidence_reference", sa.String(500), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("declared_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["bc_shops.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "relationship IN ('owned', 'authorized', 'competitor_research', 'unknown')",
            name="ck_bc_shop_access_relationship"),
        sa.CheckConstraint("status IN ('pending_review','recorded_only')",
                           name="ck_bc_shop_access_status"),
    )
    op.create_index("ix_bc_shop_access_tenant_shop_date",
                    "bc_shop_access_declarations", ["tenant_id", "shop_id", "created_at"])


def downgrade():
    # Operator audit declarations are durable and should never be silently dropped.
    bind = op.get_bind()
    url = bind.engine.url
    if os.environ.get("CAM_ALLOW_ACCESS_DECLARATION_DROP_FOR_TEST") != "1" or not (
        url.database == "cam_integration" or
        (url.get_backend_name() == "sqlite" and
         (url.database or "").rsplit("/", 1)[-1] == "access-declaration-test.db")
    ):
        raise RuntimeError("Shop access declarations protected; downgrade denied")
    op.drop_index("ix_bc_shop_access_tenant_shop_date",
                  table_name="bc_shop_access_declarations")
    op.drop_table("bc_shop_access_declarations")
