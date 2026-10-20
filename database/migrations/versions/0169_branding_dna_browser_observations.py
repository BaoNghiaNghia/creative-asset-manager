"""Quarantined, deduplicated DOM-only browser observation audit.

Revision ID: 0169_branding_dna_browser_observations
Revises: 0168_branding_dna_outline_versions
"""
import os
import sqlalchemy as sa
from alembic import op

revision = "0169_branding_dna_browser_observations"
down_revision = "0168_branding_dna_outline_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_browser_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("report_key", sa.String(128), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("shop_url_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        sa.Column("imported_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["bc_shops.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "shop_id", "report_key",
                            name="uq_bc_browser_observation_report"),
        sa.CheckConstraint("status = 'preview_completed'",
                           name="ck_bc_browser_observation_status"),
        sa.CheckConstraint("platform IN ('amazon','etsy')",
                           name="ck_bc_browser_observation_platform"),
    )
    op.create_index(
        "ix_bc_browser_observations_shop_date", "bc_browser_observations",
        ["tenant_id", "shop_id", "created_at"],
    )


def downgrade():
    url = op.get_bind().engine.url
    disposable = (url.database == "cam_integration"
                  or (url.get_backend_name() == "sqlite" and
                      (url.database or "").rsplit("/", 1)[-1] == "browser-observation-test.db"))
    if os.getenv("CAM_ALLOW_BROWSER_OBSERVATION_DROP_FOR_TEST") != "1" or not disposable:
        raise RuntimeError("Quarantined browser observation history is protected; downgrade denied")
    op.drop_index("ix_bc_browser_observations_shop_date", table_name="bc_browser_observations")
    op.drop_table("bc_browser_observations")
