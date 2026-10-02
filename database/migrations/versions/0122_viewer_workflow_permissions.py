"""Grant Viewer access to search-adjacent review workflows.

Revision ID: 0122_viewer_workflow_permissions
Revises: 0121_rrugc_reference_sets
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "0122_viewer_workflow_permissions"
down_revision = "0121_rrugc_reference_sets"
branch_labels = None
depends_on = None


VIEWER_DESCRIPTION = (
    "Read and search assigned assets, operate Realistic Review UGC, "
    "and fully use Review Board workflows"
)
PERMISSIONS = {
    "public_review.read": "Read tenant review board issues",
    "public_review.resolve": "Resolve tenant review board issues",
    "realistic_review_ugc.read": "Read Realistic Review UGC workflows",
    "realistic_review_ugc.run": "Run Realistic Review UGC workflows",
    "realistic_review_ugc.configure": "Configure Realistic Review UGC workflows",
}


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
    roles = sa.table(
        "roles",
        sa.column("id", sa.String),
        sa.column("role_key", sa.String),
        sa.column("description", sa.Text),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.String),
        sa.column("role_id", sa.String),
        sa.column("permission_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    permission_ids: dict[str, str] = {}
    for key, description in PERMISSIONS.items():
        permission_id = bind.scalar(
            sa.select(permissions.c.id).where(permissions.c.permission_key == key)
        )
        if permission_id is None:
            permission_id = str(uuid4())
            bind.execute(
                permissions.insert().values(
                    id=permission_id,
                    permission_key=key,
                    description=description,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            bind.execute(
                permissions.update()
                .where(permissions.c.id == permission_id)
                .values(description=description, status="active", updated_at=now)
            )
        permission_ids[key] = str(permission_id)

    viewer_role_ids = list(
        bind.scalars(sa.select(roles.c.id).where(roles.c.role_key == "viewer"))
    )
    if not viewer_role_ids:
        return

    bind.execute(
        roles.update()
        .where(roles.c.id.in_(viewer_role_ids))
        .values(description=VIEWER_DESCRIPTION, updated_at=now)
    )

    existing = set(
        bind.execute(
            sa.select(role_permissions.c.role_id, role_permissions.c.permission_id).where(
                role_permissions.c.role_id.in_(viewer_role_ids),
                role_permissions.c.permission_id.in_(list(permission_ids.values())),
            )
        ).all()
    )
    for role_id in viewer_role_ids:
        for permission_id in permission_ids.values():
            if (role_id, permission_id) in existing:
                continue
            bind.execute(
                role_permissions.insert().values(
                    id=str(uuid4()),
                    role_id=role_id,
                    permission_id=permission_id,
                    created_at=now,
                )
            )


def downgrade() -> None:
    # Keep Viewer grants on rollback so active review workflows are not revoked.
    pass
