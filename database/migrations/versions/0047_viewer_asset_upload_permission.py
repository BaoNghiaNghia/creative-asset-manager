"""Grant the dedicated upload permission to existing Viewer roles.

Revision ID: 0047_viewer_upload
Revises: 0046_asset_pipeline_fk_detach
"""
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "0047_viewer_upload"
down_revision = "0046_asset_pipeline_fk_detach"
branch_labels = None
depends_on = None


PERMISSION_KEY = "assets.upload"
PERMISSION_DESCRIPTION = "Upload files to authorized tenant folders"


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.String),
        sa.column("permission_key", sa.String),
        sa.column("description", sa.Text),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    roles = sa.table("roles", sa.column("id", sa.String), sa.column("role_key", sa.String))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.String),
        sa.column("role_id", sa.String),
        sa.column("permission_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    permission_id = bind.scalar(
        sa.select(permissions.c.id).where(permissions.c.permission_key == PERMISSION_KEY)
    )
    if permission_id is None:
        permission_id = str(uuid4())
        bind.execute(permissions.insert().values(
            id=permission_id,
            permission_key=PERMISSION_KEY,
            description=PERMISSION_DESCRIPTION,
            status="active",
            created_at=now,
            updated_at=now,
        ))
    else:
        bind.execute(permissions.update().where(permissions.c.id == permission_id).values(
            description=PERMISSION_DESCRIPTION,
            status="active",
            updated_at=now,
        ))

    viewer_role_ids = list(bind.scalars(sa.select(roles.c.id).where(roles.c.role_key == "viewer")))
    if not viewer_role_ids:
        return
    assigned_role_ids = set(bind.scalars(sa.select(role_permissions.c.role_id).where(
        role_permissions.c.role_id.in_(viewer_role_ids),
        role_permissions.c.permission_id == permission_id,
    )))
    for role_id in viewer_role_ids:
        if role_id not in assigned_role_ids:
            bind.execute(role_permissions.insert().values(
                id=str(uuid4()), role_id=role_id, permission_id=permission_id, created_at=now,
            ))


def downgrade() -> None:
    # Retain grants on rollback so existing Viewer uploads are not revoked unexpectedly.
    pass
