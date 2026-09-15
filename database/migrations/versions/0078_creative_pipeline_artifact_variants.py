"""add Creative Pipeline artifact variant identity

Revision ID: 0078_creative_pipeline_artifact_variants
Revises: 0077_creative_pipeline_knowledge_snapshot
"""
from alembic import op
import sqlalchemy as sa
revision="0078_cp_artifact_variants"
down_revision="0077_creative_pipeline_knowledge_snapshot"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("creative_pipeline_artifacts", sa.Column("variant_key", sa.String(128), nullable=True))
    op.drop_index("uq_cp_artifacts_logical_no_ratio", table_name="creative_pipeline_artifacts")
    op.drop_index("uq_cp_artifacts_logical_ratio", table_name="creative_pipeline_artifacts")
    op.create_index("uq_cp_artifacts_logical_no_ratio", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version"], unique=True, postgresql_where=sa.text("aspect_ratio IS NULL AND variant_key IS NULL"), sqlite_where=sa.text("aspect_ratio IS NULL AND variant_key IS NULL"))
    op.create_index("uq_cp_artifacts_logical_no_ratio_variant", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version","variant_key"], unique=True, postgresql_where=sa.text("aspect_ratio IS NULL AND variant_key IS NOT NULL"), sqlite_where=sa.text("aspect_ratio IS NULL AND variant_key IS NOT NULL"))
    op.create_index("uq_cp_artifacts_logical_ratio", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version","aspect_ratio"], unique=True, postgresql_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NULL"), sqlite_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NULL"))
    op.create_index("uq_cp_artifacts_logical_ratio_variant", "creative_pipeline_artifacts", ["tenant_id","pipeline_run_id","artifact_type","version","aspect_ratio","variant_key"], unique=True, postgresql_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL"), sqlite_where=sa.text("aspect_ratio IS NOT NULL AND variant_key IS NOT NULL"))

def downgrade():
    for name in ("uq_cp_artifacts_logical_ratio_variant","uq_cp_artifacts_logical_ratio","uq_cp_artifacts_logical_no_ratio_variant","uq_cp_artifacts_logical_no_ratio"):
        op.drop_index(name, table_name="creative_pipeline_artifacts")
    op.drop_column("creative_pipeline_artifacts","variant_key")
    op.create_index("uq_cp_artifacts_logical_ratio","creative_pipeline_artifacts",["tenant_id","pipeline_run_id","artifact_type","version","aspect_ratio"],unique=True,postgresql_where=sa.text("aspect_ratio IS NOT NULL"),sqlite_where=sa.text("aspect_ratio IS NOT NULL"))
    op.create_index("uq_cp_artifacts_logical_no_ratio","creative_pipeline_artifacts",["tenant_id","pipeline_run_id","artifact_type","version"],unique=True,postgresql_where=sa.text("aspect_ratio IS NULL"),sqlite_where=sa.text("aspect_ratio IS NULL"))
