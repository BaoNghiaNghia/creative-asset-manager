"""Bind RRUGC campaigns to product snapshots and add generation-attempt provenance.

Revision ID: 0096_rrugc_generation_foundation
Revises: 0095_rrugc_product_registry
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0096_rrugc_generation_foundation"
down_revision = "0095_rrugc_product_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rrugc_campaigns", sa.Column("product_id", sa.String(length=36)))
    op.add_column("rrugc_campaigns", sa.Column("product_revision", sa.Integer()))
    op.add_column("rrugc_campaigns", sa.Column("product_snapshot_json", sa.JSON()))
    op.add_column(
        "rrugc_campaigns",
        sa.Column("product_reference_snapshot_json", sa.JSON()),
    )
    op.add_column(
        "rrugc_campaigns",
        sa.Column("product_bound_at", sa.DateTime(timezone=True)),
    )
    op.create_foreign_key(
        "fk_rrugc_campaign_product",
        "rrugc_campaigns",
        "rrugc_products",
        ["tenant_id", "product_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_rrugc_campaign_product",
        "rrugc_campaigns",
        ["tenant_id", "product_id", "updated_at"],
    )

    op.create_unique_constraint(
        "uq_rrugc_candidate_tenant_id",
        "rrugc_candidates",
        ["tenant_id", "id"],
    )

    op.create_table(
        "rrugc_generation_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("product_revision", sa.Integer(), nullable=False),
        sa.Column("product_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("product_reference_snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "generation_variant", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("worker_skill_version", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("provider_model", sa.String(length=128)),
        sa.Column("prompt_text", sa.Text()),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="prepared"
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_generation_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["rrugc_candidates.tenant_id", "rrugc_candidates.id"],
            name="fk_rrugc_generation_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_generation_product",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_generation_attempt_idempotency",
        ),
    )
    op.create_index(
        "ix_rrugc_generation_campaign_status",
        "rrugc_generation_attempts",
        ["tenant_id", "campaign_id", "status", "created_at"],
    )
    op.create_index(
        "ix_rrugc_generation_candidate",
        "rrugc_generation_attempts",
        ["tenant_id", "candidate_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rrugc_generation_candidate",
        table_name="rrugc_generation_attempts",
    )
    op.drop_index(
        "ix_rrugc_generation_campaign_status",
        table_name="rrugc_generation_attempts",
    )
    op.drop_table("rrugc_generation_attempts")

    op.drop_constraint(
        "uq_rrugc_candidate_tenant_id",
        "rrugc_candidates",
        type_="unique",
    )

    op.drop_index("ix_rrugc_campaign_product", table_name="rrugc_campaigns")
    op.drop_constraint(
        "fk_rrugc_campaign_product",
        "rrugc_campaigns",
        type_="foreignkey",
    )
    op.drop_column("rrugc_campaigns", "product_bound_at")
    op.drop_column("rrugc_campaigns", "product_reference_snapshot_json")
    op.drop_column("rrugc_campaigns", "product_snapshot_json")
    op.drop_column("rrugc_campaigns", "product_revision")
    op.drop_column("rrugc_campaigns", "product_id")
