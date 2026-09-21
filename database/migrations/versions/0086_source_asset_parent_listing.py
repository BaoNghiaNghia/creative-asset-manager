"""Index source-asset parent metadata for fast Public Review folder paging.

Revision ID: 0086_source_asset_parent_listing
Revises: 0085_r2_multipart_upload_id_text
"""

import sqlalchemy as sa
from alembic import op

revision = "0086_source_asset_parent_listing"
down_revision = "0085_r2_multipart_upload_id_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_assets",
        sa.Column("parent_external_id", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "source_assets",
        sa.Column("is_folder", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(
            """
            UPDATE source_assets
            SET parent_external_id = COALESCE(
                    NULLIF(source_metadata ->> 'parent_id', ''),
                    NULLIF(source_metadata -> 'parents' ->> 0, '')
                ),
                is_folder = CASE
                    WHEN lower(COALESCE(source_metadata ->> 'is_folder', 'false')) = 'true' THEN true
                    WHEN mime_type IN (
                        'application/vnd.google-apps.folder',
                        'application/vnd.microsoft.folder',
                        'application/vnd.microsoft.sharepoint.folder'
                    ) THEN true
                    ELSE false
                END
            """
        ))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text(
            """
            UPDATE source_assets
            SET parent_external_id = COALESCE(
                    NULLIF(json_extract(source_metadata, '$.parent_id'), ''),
                    NULLIF(json_extract(source_metadata, '$.parents[0]'), '')
                ),
                is_folder = CASE
                    WHEN lower(COALESCE(json_extract(source_metadata, '$.is_folder'), 'false')) IN ('1', 'true') THEN 1
                    WHEN mime_type IN (
                        'application/vnd.google-apps.folder',
                        'application/vnd.microsoft.folder',
                        'application/vnd.microsoft.sharepoint.folder'
                    ) THEN 1
                    ELSE 0
                END
            """
        ))

    op.create_index(
        "ix_source_assets_parent_listing",
        "source_assets",
        ["tenant_id", "external_source_id", "parent_external_id", "deleted_at", "is_folder"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_assets_parent_listing", table_name="source_assets")
    op.drop_column("source_assets", "is_folder")
    op.drop_column("source_assets", "parent_external_id")
