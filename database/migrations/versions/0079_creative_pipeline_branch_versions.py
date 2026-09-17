"""add Creative Pipeline branch lineage and listing-global versions

Revision ID: 0079_cp_branch_versions
Revises: 0078_cp_artifact_variants
"""
from alembic import op
import sqlalchemy as sa

revision = "0079_cp_branch_versions"
down_revision = "0078_cp_artifact_variants"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("creative_pipeline_runs", sa.Column("parent_run_id", sa.String(36), nullable=True))
    op.add_column("creative_pipeline_runs", sa.Column("branch_start_node", sa.String(48), nullable=True))
    op.add_column("creative_pipeline_runs", sa.Column("operator_idempotency_key", sa.String(255), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("creative_pipeline_runs") as batch:
            batch.create_foreign_key("fk_cp_runs_tenant_parent", "creative_pipeline_runs", ["tenant_id", "parent_run_id"], ["tenant_id", "id"], ondelete="RESTRICT")
            batch.create_check_constraint("ck_cp_runs_branch_start_node", "branch_start_node IS NULL OR branch_start_node IN ('input_data','idea_story','prompt','video_generation')")
    else:
        op.create_foreign_key("fk_cp_runs_tenant_parent", "creative_pipeline_runs", "creative_pipeline_runs", ["tenant_id", "parent_run_id"], ["tenant_id", "id"], ondelete="RESTRICT")
        op.create_check_constraint("ck_cp_runs_branch_start_node", "creative_pipeline_runs", "branch_start_node IS NULL OR branch_start_node IN ('input_data','idea_story','prompt','video_generation')")
    op.create_index("ix_cp_runs_tenant_parent", "creative_pipeline_runs", ["tenant_id", "parent_run_id"])
    op.create_index("uq_cp_runs_tenant_listing_idempotency", "creative_pipeline_runs", ["tenant_id", "listing_task_id", "operator_idempotency_key"], unique=True, postgresql_where=sa.text("operator_idempotency_key IS NOT NULL"), sqlite_where=sa.text("operator_idempotency_key IS NOT NULL"))

    op.add_column("creative_pipeline_generation_runs", sa.Column("listing_task_id", sa.String(36), nullable=True))
    op.execute(sa.text("""UPDATE creative_pipeline_generation_runs AS g SET listing_task_id = r.listing_task_id FROM creative_pipeline_runs AS r WHERE g.tenant_id = r.tenant_id AND g.pipeline_run_id = r.id"""))
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("creative_pipeline_generation_runs") as batch:
            batch.create_foreign_key("fk_cp_generation_runs_tenant_listing", "creative_pipeline_listing_tasks", ["tenant_id", "listing_task_id"], ["tenant_id", "id"], ondelete="CASCADE")
            batch.alter_column("listing_task_id", existing_type=sa.String(length=36), nullable=False)
            batch.drop_constraint("uq_cp_generation_runs_logical_generation", type_="unique")
            batch.create_unique_constraint("uq_cp_generation_runs_logical_generation", ["tenant_id", "listing_task_id", "provider", "model", "aspect_ratio", "generation_number"])
    else:
        op.create_foreign_key("fk_cp_generation_runs_tenant_listing", "creative_pipeline_generation_runs", "creative_pipeline_listing_tasks", ["tenant_id", "listing_task_id"], ["tenant_id", "id"], ondelete="CASCADE")
        op.alter_column("creative_pipeline_generation_runs", "listing_task_id", nullable=False)
        op.drop_constraint("uq_cp_generation_runs_logical_generation", "creative_pipeline_generation_runs", type_="unique")
        op.create_unique_constraint("uq_cp_generation_runs_logical_generation", "creative_pipeline_generation_runs", ["tenant_id", "listing_task_id", "provider", "model", "aspect_ratio", "generation_number"])

    for name in ("uq_cp_artifacts_logical_ratio_variant","uq_cp_artifacts_logical_ratio","uq_cp_artifacts_logical_no_ratio_variant","uq_cp_artifacts_logical_no_ratio"):
        op.drop_index(name, table_name="creative_pipeline_artifacts")
    op.create_index("uq_cp_artifacts_logical_no_ratio", "creative_pipeline_artifacts", ["tenant_id","listing_task_id","artifact_type","version"], unique=True, postgresql_where=sa.text("aspect_ratio IS NULL AND variant_key IS NULL"), sqlite_where=sa.text("aspect_ratio IS NULL AND variant_key IS NULL"))
    op.create_index("uq_cp_artifacts_logical_no_ratio_variant", "creative_pipeline_artifacts", ["tenant_id","listing_task_id","artifact_type","version","variant_key"], unique=True, postgresql_where=sa.text("aspect_ratio IS NULL AND variant_key IS NOT NULL"), sqlite_where=sa.text("aspect_ratio IS NULL AND variant_key IS NOT NULL"))
    op.create_index("uq_cp_artifacts_logical_ratio", "creative_pipeline_artifacts", ["tenant_id","listing_task_id","artifact_type","version","aspect_ratio"], unique=True, postgresql_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NULL"), sqlite_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NULL"))
    op.create_index("uq_cp_artifacts_logical_ratio_variant", "creative_pipeline_artifacts", ["tenant_id","listing_task_id","artifact_type","version","aspect_ratio","variant_key"], unique=True, postgresql_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL"))

def downgrade():
    for name in ("uq_cp_artifacts_logical_ratio_variant","uq_cp_artifacts_logical_ratio","uq_cp_artifacts_logical_no_ratio_variant","uq_cp_artifacts_logical_no_ratio"):
        op.drop_index(name, table_name="creative_pipeline_artifacts")
    op.create_index("uq_cp_artifacts_logical_no_ratio", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version"], unique=True, postgresql_where=sa.text("aspect_ratio IS NULL AND variant_key IS NULL"), sqlite_where=sa.text("aspect_ratio IS NULL AND variant_key IS NULL"))
    op.create_index("uq_cp_artifacts_logical_no_ratio_variant", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version","variant_key"], unique=True, postgresql_where=sa.text("aspect_ratio IS NULL AND variant_key IS NOT NULL"), sqlite_where=sa.text("aspect_ratio IS NULL AND variant_key IS NOT NULL"))
    op.create_index("uq_cp_artifacts_logical_ratio", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version","aspect_ratio"], unique=True, postgresql_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NULL"), sqlite_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NULL"))
    op.create_index("uq_cp_artifacts_logical_ratio_variant", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version","aspect_ratio","variant_key"], unique=True, postgresql_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL"), sqlite_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL"))
    op.drop_constraint("uq_cp_generation_runs_logical_generation", "creative_pipeline_generation_runs", type_="unique")
    op.create_unique_constraint("uq_cp_generation_runs_logical_generation", "creative_pipeline_generation_runs", ["tenant_id","pipeline_run_id","provider","model","aspect_ratio","generation_number"])
    op.drop_constraint("fk_cp_generation_runs_tenant_listing", "creative_pipeline_generation_runs", type_="foreignkey")
    op.drop_column("creative_pipeline_generation_runs", "listing_task_id")
    op.drop_constraint("ck_cp_runs_branch_start_node", "creative_pipeline_runs", type_="check")
    op.drop_index("uq_cp_runs_tenant_listing_idempotency", table_name="creative_pipeline_runs")
    op.drop_index("ix_cp_runs_tenant_parent", table_name="creative_pipeline_runs")
    op.drop_constraint("fk_cp_runs_tenant_parent", "creative_pipeline_runs", type_="foreignkey")
    op.drop_column("creative_pipeline_runs", "operator_idempotency_key")
    op.drop_column("creative_pipeline_runs", "branch_start_node")
    op.drop_column("creative_pipeline_runs", "parent_run_id")
