"""Adopt legacy tag and asset metadata tables into Alembic.

Revision ID: 0018_legacy_metadata_schema
Revises: 0017_search_lifecycle_states
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_legacy_metadata_schema"
down_revision = "0017_search_lifecycle_states"
branch_labels = None
depends_on = None


def _index_names(inspector, table_name: str) -> set[str]:
    return {
        index["name"]
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tags" not in tables:
        op.create_table(
            "tags",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("color", sa.String(length=16), nullable=False),
            sa.Column("group_key", sa.String(length=64), nullable=True),
            sa.Column("is_system", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("name", name="uq_tags_name"),
        )
        tables.add("tags")

    if "asset_metadata" not in tables:
        op.create_table(
            "asset_metadata",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("account_id", sa.String(length=255), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("item_id", sa.String(length=2048), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "account_id",
                "provider",
                "item_id",
                name="uq_asset_identity",
            ),
            sa.CheckConstraint(
                "rating IS NULL OR (rating >= 1 AND rating <= 5)",
                name="ck_asset_rating",
            ),
        )
        tables.add("asset_metadata")

    inspector = sa.inspect(bind)
    metadata_indexes = _index_names(inspector, "asset_metadata")
    for name, columns in (
        ("ix_asset_metadata_account_id", ["account_id"]),
        ("ix_asset_metadata_provider", ["provider"]),
        ("ix_asset_metadata_item_id", ["item_id"]),
    ):
        if name not in metadata_indexes:
            op.create_index(name, "asset_metadata", columns)

    if "asset_tag_assignments" not in tables:
        op.create_table(
            "asset_tag_assignments",
            sa.Column("asset_metadata_id", sa.Integer(), nullable=False),
            sa.Column("tag_id", sa.String(length=64), nullable=False),
            sa.ForeignKeyConstraint(
                ["asset_metadata_id"],
                ["asset_metadata.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tag_id"],
                ["tags.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("asset_metadata_id", "tag_id"),
        )


def downgrade() -> None:
    # These tables existed before Alembic ownership and may contain authoritative
    # user metadata. A code rollback must not destroy them.
    pass
