"""Create dynamic AI metadata profiles and analysis history.

Revision ID: 0004_dynamic_ai_metadata
Revises: 0003_managed_asset_storage

Rollback: export analysis history before downgrade. The downgrade drops only
Step 09 tables and does not change legacy asset_metadata/tag data.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_dynamic_ai_metadata"
down_revision = "0003_managed_asset_storage"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "metadata_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("profile_name", sa.String(255), nullable=False),
        sa.Column("profile_version", sa.String(100), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("optional_json_schema", JSON_DOCUMENT),
        sa.Column("search_config_json", JSON_DOCUMENT, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "profile_name", "profile_version",
            name="uq_metadata_profiles_tenant_name_version",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_metadata_profiles_tenant_id"),
    )
    op.create_index("ix_metadata_profiles_active", "metadata_profiles", ["tenant_id", "active"])

    op.create_table(
        "asset_ai_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("asset_id", sa.String(36), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metadata_profile_id", sa.String(36), nullable=False),
        sa.Column("metadata_profile", sa.String(255), nullable=False),
        sa.Column("metadata_profile_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("pipeline_version", sa.String(100), nullable=False),
        sa.Column("ai_provider", sa.String(100)),
        sa.Column("ai_model", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("raw_response_json", JSON_DOCUMENT),
        sa.Column("metadata_json", JSON_DOCUMENT),
        sa.Column("search_projection", JSON_DOCUMENT),
        sa.Column("search_projection_version", sa.String(100)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"],
            ondelete="CASCADE", name="fk_asset_ai_analyses_tenant_asset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metadata_profile_id"],
            ["metadata_profiles.tenant_id", "metadata_profiles.id"],
            ondelete="RESTRICT", name="fk_asset_ai_analyses_tenant_profile",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_asset_ai_analyses_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_asset_ai_analyses_attempt_count"),
    )
    op.create_index(
        "uq_asset_ai_analyses_normal_run",
        "asset_ai_analyses",
        [
            "tenant_id", "asset_id", "content_hash", "metadata_profile_id",
            "prompt_version", "pipeline_version",
        ],
        unique=True,
        postgresql_where=sa.text("forced = false"),
        sqlite_where=sa.text("forced = 0"),
    )
    op.create_index(
        "ix_asset_ai_analyses_history",
        "asset_ai_analyses",
        ["tenant_id", "asset_id", "created_at"],
    )
    op.create_index(
        "ix_asset_ai_analyses_status", "asset_ai_analyses", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_asset_ai_analyses_status", table_name="asset_ai_analyses")
    op.drop_index("ix_asset_ai_analyses_history", table_name="asset_ai_analyses")
    op.drop_index("uq_asset_ai_analyses_normal_run", table_name="asset_ai_analyses")
    op.drop_table("asset_ai_analyses")
    op.drop_index("ix_metadata_profiles_active", table_name="metadata_profiles")
    op.drop_table("metadata_profiles")
