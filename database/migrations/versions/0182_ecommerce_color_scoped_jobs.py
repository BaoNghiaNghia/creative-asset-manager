"""Scope Ecommerce single-photo jobs by the selected 8869 color without touching outputs.

Revision ID: 0182_ecommerce_color_scoped_jobs
Revises: 0181_provider_policy_json_wakeup
"""
import sqlalchemy as sa
from alembic import op

revision = "0182_ecommerce_color_scoped_jobs"
down_revision = "0181_provider_policy_json_wakeup"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rrugc_ecommerce_jobs", sa.Column(
        "color_key", sa.String(80), nullable=False, server_default="natural-black"))
    # Preserve all historical rows as Natural/Black; never delete/requeue outputs.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite needs a reflected source table for a constrained rebuild.
        source = sa.Table("rrugc_ecommerce_jobs", sa.MetaData(), autoload_with=bind)
        # SQLite may lose a constraint name after ALTER TABLE ADD COLUMN;
        # identify the legacy uniqueness by its precise column signature.
        prior_columns = ("tenant_id", "source_plan_id", "source_revision", "stage")
        prior = next((constraint for constraint in source.constraints
                      if isinstance(constraint, sa.UniqueConstraint)
                      and tuple(col.name for col in constraint.columns) == prior_columns), None)
        if prior is None:
            raise RuntimeError("existing_ecommerce_unique_constraint_missing")
        source.constraints.remove(prior)
        with op.batch_alter_table("rrugc_ecommerce_jobs", copy_from=source, recreate="always") as batch:
            batch.create_unique_constraint(
                "uq_rrugc_ecommerce_source_revision_stage_color",
                ["tenant_id", "source_plan_id", "source_revision", "stage", "color_key"])
    else:
        op.drop_constraint("uq_rrugc_ecommerce_source_revision_stage",
                           "rrugc_ecommerce_jobs", type_="unique")
        op.create_unique_constraint("uq_rrugc_ecommerce_source_revision_stage_color",
                                    "rrugc_ecommerce_jobs",
                                    ["tenant_id", "source_plan_id", "source_revision", "stage", "color_key"])


def downgrade():
    conn = op.get_bind()
    if conn.scalar(sa.text(
        "SELECT count(*) FROM rrugc_ecommerce_jobs WHERE color_key <> 'natural-black'"
    )):
        raise RuntimeError("cannot_remove_color_scoping_with_nondefault_jobs")
    with op.batch_alter_table("rrugc_ecommerce_jobs") as batch:
        batch.drop_constraint("uq_rrugc_ecommerce_source_revision_stage_color", type_="unique")
        batch.create_unique_constraint(
            "uq_rrugc_ecommerce_source_revision_stage",
            ["tenant_id", "source_plan_id", "source_revision", "stage"])
        batch.drop_column("color_key")
