"""Versioned fact outlines and append-only review events.

Revision ID: 0168_branding_dna_outline_versions
Revises: 0167_branding_dna_evidence_assessments
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0168_branding_dna_outline_versions"
down_revision = "0167_branding_dna_evidence_assessments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_dna_outline_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("outline_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["bc_shops.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "tenant_id", "shop_id", "version_number",
            name="uq_bc_dna_outline_shop_version",
        ),
        sa.UniqueConstraint(
            "tenant_id", "shop_id", "input_fingerprint",
            name="uq_bc_dna_outline_fingerprint",
        ),
    )
    op.create_index(
        "ix_bc_dna_outline_tenant_shop", "bc_dna_outline_versions",
        ["tenant_id", "shop_id", "version_number"],
    )
    op.create_table(
        "bc_dna_outline_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"], ["bc_dna_outline_versions.id"], ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('needs_changes','fact_outline_reviewed','rejected')",
            name="ck_bc_dna_outline_review_decision",
        ),
    )
    op.create_index(
        "ix_bc_dna_outline_reviews_date", "bc_dna_outline_reviews",
        ["version_id", "created_at"],
    )


def downgrade():
    url = op.get_bind().engine.url
    disposable = (
        url.database == "cam_integration"
        or (url.get_backend_name() == "sqlite"
            and (url.database or "").rsplit("/", 1)[-1] == "dna-outline-test.db")
    )
    if os.environ.get("CAM_ALLOW_DNA_OUTLINE_DROP_FOR_TEST") != "1" or not disposable:
        raise RuntimeError("Versioned DNA outline audit history protected; downgrade denied")
    op.drop_index("ix_bc_dna_outline_reviews_date", table_name="bc_dna_outline_reviews")
    op.drop_table("bc_dna_outline_reviews")
    op.drop_index("ix_bc_dna_outline_tenant_shop", table_name="bc_dna_outline_versions")
    op.drop_table("bc_dna_outline_versions")
