"""Include AI provider and model in normal-analysis idempotency.

Revision ID: 0019_ai_provider_selection
Revises: 0018_legacy_metadata_schema

Downgrade restores the legacy identity. It can fail after multiple provider or
model variants have been created for the same legacy identity; operators must
remove or force-mark those variants before downgrading.
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_ai_provider_selection"
down_revision = "0018_legacy_metadata_schema"
branch_labels = None
depends_on = None

_INDEX = "uq_asset_ai_analyses_normal_run"
_LEGACY_COLUMNS = [
    "tenant_id",
    "asset_id",
    "content_hash",
    "metadata_profile_id",
    "prompt_version",
    "pipeline_version",
]
_PROVIDER_COLUMNS = [
    *_LEGACY_COLUMNS,
    "ai_provider",
    "ai_model",
]


def _create(columns: list[str]) -> None:
    op.create_index(
        _INDEX,
        "asset_ai_analyses",
        columns,
        unique=True,
        postgresql_where=sa.text("forced = false"),
        sqlite_where=sa.text("forced = 0"),
    )


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="asset_ai_analyses")
    _create(_PROVIDER_COLUMNS)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="asset_ai_analyses")
    _create(_LEGACY_COLUMNS)
