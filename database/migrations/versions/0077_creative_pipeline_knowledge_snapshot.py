"""add durable Creative Pipeline knowledge snapshot artifact type

Revision ID: 0077_creative_pipeline_knowledge_snapshot
Revises: 0076_creative_pipeline_artifact_storage
"""
from alembic import op
import sqlalchemy as sa

revision = "0077_creative_pipeline_knowledge_snapshot"
down_revision = "0076_creative_pipeline_artifact_storage"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("creative_pipeline_artifacts") as batch:
        batch.drop_constraint("ck_cp_artifacts_type", type_="check")
        batch.create_check_constraint(
            "ck_cp_artifacts_type",
            "artifact_type IN ('input_snapshot', 'input_manifest', 'knowledge_snapshot', 'idea_story', 'prompt', 'generation_metadata', 'raw_video', 'enhanced_video')",
        )

def downgrade():
    with op.batch_alter_table("creative_pipeline_artifacts") as batch:
        batch.drop_constraint("ck_cp_artifacts_type", type_="check")
        batch.create_check_constraint(
            "ck_cp_artifacts_type",
            "artifact_type IN ('input_snapshot', 'input_manifest', 'idea_story', 'prompt', 'generation_metadata', 'raw_video', 'enhanced_video')",
        )
