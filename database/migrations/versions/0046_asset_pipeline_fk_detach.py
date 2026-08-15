"""Detach optional asset-pipeline references without nulling tenant scope.

Revision ID: 0046_asset_pipeline_fk_detach
Revises: 0045_managed_storage_cleanup

PostgreSQL supports column-specific ``ON DELETE SET NULL`` actions.  The
previous composite actions nulled both tenant_id and the optional reference,
which conflicts with asset_pipelines.tenant_id being intentionally NOT NULL.
"""

from alembic import op


revision = "0046_asset_pipeline_fk_detach"
down_revision = "0045_managed_storage_cleanup"
branch_labels = None
depends_on = None


def _replace_foreign_key(*, name: str, target: list[str], ondelete: str) -> None:
    op.drop_constraint(name, "asset_pipelines", type_="foreignkey")
    op.create_foreign_key(
        name,
        "asset_pipelines",
        target[0],
        ["tenant_id", target[1]],
        ["tenant_id", "id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    # SQLite does not support column-specific SET NULL.  PostgreSQL is the
    # production/CI database for this tenant-isolation invariant; SQLite test
    # schemas retain their existing generic action.
    if op.get_bind().dialect.name != "postgresql":
        return

    _replace_foreign_key(
        name="fk_asset_pipelines_source_asset",
        target=["source_assets", "source_asset_id"],
        ondelete="SET NULL (source_asset_id)",
    )
    _replace_foreign_key(
        name="fk_asset_pipelines_asset",
        target=["assets", "asset_id"],
        ondelete="SET NULL (asset_id)",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    _replace_foreign_key(
        name="fk_asset_pipelines_source_asset",
        target=["source_assets", "source_asset_id"],
        ondelete="SET NULL",
    )
    _replace_foreign_key(
        name="fk_asset_pipelines_asset",
        target=["assets", "asset_id"],
        ondelete="SET NULL",
    )
