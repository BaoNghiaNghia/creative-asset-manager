"""Grant the dedicated delete permission to existing Viewer roles.

Revision ID: 0048_viewer_delete
Revises: 0047_viewer_upload
"""
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "0048_viewer_delete"
down_revision = "0047_viewer_upload"
branch_labels = None
depends_on = None


PERMISSION_KEY = "assets.delete"
PERMISSION_DESCRIPTION = "Delete files from authorized tenant folders"


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
            id=permission_id, permission_key=PERMISSION_KEY,
            description=PERMISSION_DESCRIPTION, status="active",
            created_at=now, updated_at=now,
        ))
    viewer_role_ids = list(bind.scalars(sa.select(roles.c.id).where(roles.c.role_key == "viewer")))
    assigned = set() if not viewer_role_ids else set(bind.scalars(sa.select(role_permissions.c.role_id).where(
        role_permissions.c.role_id.in_(viewer_role_ids),
        role_permissions.c.permission_id == permission_id,
    )))
    for role_id in viewer_role_ids:
        if role_id not in assigned:
            bind.execute(role_permissions.insert().values(
                id=str(uuid4()), role_id=role_id, permission_id=permission_id, created_at=now,
            ))


def downgrade() -> None:
    # Retain existing grants on rollback to avoid unexpectedly revoking access.
    pass
