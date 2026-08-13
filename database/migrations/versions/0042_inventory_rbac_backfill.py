"""Backfill Inventory credential permissions for existing tenant admins.

Revision ID: 0042_inventory_rbac_backfill
Revises: 0041_inventory_ai_cred
"""
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "0042_inventory_rbac_backfill"
down_revision = "0041_inventory_ai_cred"
branch_labels = None
depends_on = None


# Keep this migration deliberately narrow: only the two permissions needed for
# Gemini credential visibility and rotation are granted to existing tenant admins.
CREDENTIAL_PERMISSIONS = {
    "inventory.read": "Read tenant Inventory operations",
    "inventory.credentials.manage": "Manage tenant Inventory Gemini credentials",
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
    roles = sa.table("roles", sa.column("id", sa.String), sa.column("role_key", sa.String))
    role_permissions = sa.table(
        "role_permissions",
        sa.column("id", sa.String),
        sa.column("role_id", sa.String),
        sa.column("permission_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    existing = {
        row.permission_key: row.id
        for row in bind.execute(
            sa.select(permissions.c.id, permissions.c.permission_key).where(
                permissions.c.permission_key.in_(CREDENTIAL_PERMISSIONS)
            )
        )
    }
    for key, description in CREDENTIAL_PERMISSIONS.items():
        if key not in existing:
            permission_id = str(uuid4())
            bind.execute(permissions.insert().values(
                id=permission_id, permission_key=key, description=description,
                status="active", created_at=now, updated_at=now,
            ))
            existing[key] = permission_id

    tenant_admin_ids = [
        row.id for row in bind.execute(
            sa.select(roles.c.id).where(roles.c.role_key == "tenant_admin")
        )
    ]
    if not tenant_admin_ids:
        return
    assigned = {
        (row.role_id, row.permission_id)
        for row in bind.execute(
            sa.select(role_permissions.c.role_id, role_permissions.c.permission_id).where(
                role_permissions.c.role_id.in_(tenant_admin_ids),
                role_permissions.c.permission_id.in_(list(existing.values())),
            )
        )
    }
    for role_id in tenant_admin_ids:
        for permission_id in existing.values():
            if (role_id, permission_id) not in assigned:
                bind.execute(role_permissions.insert().values(
                    id=str(uuid4()), role_id=role_id, permission_id=permission_id,
                    created_at=now,
                ))


def downgrade() -> None:
    # Role grants are durable authorization data. Removing them on a code
    # rollback can unexpectedly lock existing administrators out of credentials.
    pass
