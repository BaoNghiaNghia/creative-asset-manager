"""Enforce exact active-analysis ownership.

Revision ID: 0016_active_analysis_integrity
Revises: 0015_search_governance

Rollback restores the Step 33 analysis-id-only foreign key. Active pointers and
their append-only audit history are preserved in both directions.
"""

from alembic import op


revision = "0016_active_analysis_integrity"
down_revision = "0015_search_governance"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("asset_ai_analyses") as batch:
        batch.create_unique_constraint(
            "uq_asset_ai_analyses_active_reference",
            ["tenant_id", "asset_id", "metadata_profile_id", "id"],
        )

    with op.batch_alter_table("active_asset_analyses") as batch:
        batch.drop_constraint(
            "fk_active_analysis_analysis",
            type_="foreignkey",
        )
        batch.create_foreign_key(
            "fk_active_analysis_exact_analysis",
            "asset_ai_analyses",
            [
                "tenant_id",
                "asset_id",
                "metadata_profile_id",
                "analysis_id",
            ],
            [
                "tenant_id",
                "asset_id",
                "metadata_profile_id",
                "id",
            ],
            ondelete="RESTRICT",
        )


def downgrade():
    with op.batch_alter_table("active_asset_analyses") as batch:
        batch.drop_constraint(
            "fk_active_analysis_exact_analysis",
            type_="foreignkey",
        )
        batch.create_foreign_key(
            "fk_active_analysis_analysis",
            "asset_ai_analyses",
            ["analysis_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("asset_ai_analyses") as batch:
        batch.drop_constraint(
            "uq_asset_ai_analyses_active_reference",
            type_="unique",
        )
