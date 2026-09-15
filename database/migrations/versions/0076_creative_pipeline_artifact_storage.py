"""add CP-04 pipeline storage identity and artifact lifecycle

Revision ID: 0076_creative_pipeline_artifact_storage
Revises: 0075_creative_pipeline_domain
"""
from alembic import op
import sqlalchemy as sa

revision = "0076_creative_pipeline_artifact_storage"
down_revision = "0075_creative_pipeline_domain"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("creative_pipeline_listing_tasks", sa.Column("pipeline_folder_id", sa.String(2048), nullable=True))
    op.add_column("creative_pipeline_artifacts", sa.Column("status", sa.String(24), nullable=False, server_default="reserved"))
    op.add_column("creative_pipeline_artifacts", sa.Column("external_file_id", sa.String(2048), nullable=True))
    op.add_column("creative_pipeline_artifacts", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("creative_pipeline_artifacts") as batch:
        batch.drop_constraint("uq_cp_artifacts_logical_version", type_="unique")
        batch.create_check_constraint("ck_cp_artifacts_status", "status IN ('reserved', 'available', 'inconsistent')")
    op.create_index(
        "uq_cp_artifacts_logical_ratio",
        "creative_pipeline_artifacts",
        ["tenant_id", "pipeline_run_id", "artifact_type", "version", "aspect_ratio"],
        unique=True,
        postgresql_where=sa.text("aspect_ratio IS NOT NULL"),
        sqlite_where=sa.text("aspect_ratio IS NOT NULL"),
    )
    op.create_index(
        "uq_cp_artifacts_logical_no_ratio",
        "creative_pipeline_artifacts",
        ["tenant_id", "pipeline_run_id", "artifact_type", "version"],
        unique=True,
        postgresql_where=sa.text("aspect_ratio IS NULL"),
        sqlite_where=sa.text("aspect_ratio IS NULL"),
    )


def downgrade():
    op.drop_index("uq_cp_artifacts_logical_no_ratio", table_name="creative_pipeline_artifacts")
    op.drop_index("uq_cp_artifacts_logical_ratio", table_name="creative_pipeline_artifacts")
    with op.batch_alter_table("creative_pipeline_artifacts") as batch:
        batch.create_unique_constraint("uq_cp_artifacts_logical_version", ["tenant_id", "pipeline_run_id", "artifact_type", "version", "aspect_ratio"])
        batch.drop_constraint("ck_cp_artifacts_status", type_="check")
    op.drop_column("creative_pipeline_artifacts", "available_at")
    op.drop_column("creative_pipeline_artifacts", "external_file_id")
    op.drop_column("creative_pipeline_artifacts", "status")
    op.drop_column("creative_pipeline_listing_tasks", "pipeline_folder_id")
