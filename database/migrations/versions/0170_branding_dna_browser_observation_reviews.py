"""Append-only human reviews of quarantined browser pilot observations.

Revision ID: 0170_branding_dna_browser_observation_reviews
Revises: 0169_branding_dna_browser_observations
"""
import os

import sqlalchemy as sa
from alembic import op

revision = "0170_branding_dna_browser_observation_reviews"
down_revision = "0169_branding_dna_browser_observations"
branch_labels = None
depends_on = None


def upgrade():
    # A composite FK must enforce tenant and shop scope at the database layer,
    # not just at the HTTP API. Production observations already exist.
    # SQLite does not support ALTER TABLE ADD CONSTRAINT. A unique index
    # provides the same composite parent key needed by the scoped FK, while
    # PostgreSQL keeps the named unique constraint for production.
    if op.get_bind().dialect.name == "sqlite":
        op.create_index(
            "uq_bc_browser_observation_scope", "bc_browser_observations",
            ["tenant_id", "shop_id", "id"], unique=True,
        )
    else:
        op.create_unique_constraint(
            "uq_bc_browser_observation_scope", "bc_browser_observations",
            ["tenant_id", "shop_id", "id"],
        )
    op.create_table(
        "bc_browser_observation_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("observation_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("rights_basis", sa.String(40), nullable=False),
        sa.Column("evidence_reference", sa.String(160), nullable=True),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("reviewed_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "observation_id"],
            ["bc_browser_observations.tenant_id",
             "bc_browser_observations.shop_id", "bc_browser_observations.id"],
            name="fk_bc_browser_review_observation_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('needs_authorized_source','metadata_consistent','rejected')",
            name="ck_bc_browser_review_decision",
        ),
        sa.CheckConstraint(
            "rights_basis IN ('unknown','consent_reference_recorded','permission_missing')",
            name="ck_bc_browser_review_rights_basis",
        ),
    )
    op.create_index(
        "ix_bc_browser_reviews_scope_date", "bc_browser_observation_reviews",
        ["tenant_id", "shop_id", "observation_id", "created_at"],
    )


def downgrade():
    url = op.get_bind().engine.url
    disposable = (
        url.database == "cam_integration"
        or (url.get_backend_name() == "sqlite"
            and (url.database or "").rsplit("/", 1)[-1]
            == "browser-observation-review-test.db")
    )
    if os.getenv("CAM_ALLOW_BROWSER_REVIEW_DROP_FOR_TEST") != "1" or not disposable:
        raise RuntimeError("Browser observation review audit history protected; downgrade denied")
    op.drop_index("ix_bc_browser_reviews_scope_date",
                  table_name="bc_browser_observation_reviews")
    op.drop_table("bc_browser_observation_reviews")
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("uq_bc_browser_observation_scope",
                      table_name="bc_browser_observations")
    else:
        op.drop_constraint("uq_bc_browser_observation_scope",
                           "bc_browser_observations", type_="unique")
