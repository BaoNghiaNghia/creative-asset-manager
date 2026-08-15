"""Make optional asset-pipeline references fail closed without nulling tenant scope.

Revision ID: 0046_asset_pipeline_fk_detach
Revises: 0045_managed_storage_cleanup

PostgreSQL 12 does not support a column list for SET NULL. The replacement
keeps the composite references tenant-scoped and fails closed; supported
deletion services explicitly detach only the nullable pointer inside their
transaction before deleting the parent.
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
    # SQLite migrations retain their existing schema. PostgreSQL is the
    # production database for this tenant-isolation invariant.
    if op.get_bind().dialect.name != "postgresql":
        return

    _replace_foreign_key(
        name="fk_asset_pipelines_source_asset",
        target=["source_assets", "source_asset_id"],
        ondelete="NO ACTION",
    )
    _replace_foreign_key(
        name="fk_asset_pipelines_asset",
        target=["assets", "asset_id"],
        ondelete="NO ACTION",
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
