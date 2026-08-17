from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.model import PermissionModel, RoleModel, RolePermissionModel
from app.modules.inventory.permissions import INVENTORY_PERMISSION_DEFINITIONS


PERMISSION_DEFINITIONS = {
    "assets.read": "Read tenant assets",
    "assets.upload": "Upload files to authorized tenant folders",
    "assets.delete": "Delete files from authorized tenant folders",
    "assets.manage": "Manage tenant assets",
    "ai_operations.read": "Read AI operations",
    "ai_analysis.run": "Run AI analysis",
    "ai_analysis.force": "Force a new AI analysis",
    "ai_jobs.retry": "Retry eligible AI jobs",
    "ai_jobs.cancel": "Cancel eligible AI jobs",
    "ai_provider.configure": "Configure tenant AI providers",
    "ai_budget.read": "Read tenant AI budgets",
    "ai_budget.update": "Update tenant AI budgets",
    "ai_emergency_stop": "Pause tenant AI processing",
    "tenant_members.read": "Read tenant membership",
    "tenant_members.manage": "Manage tenant membership",
    "tenant_roles.manage": "Manage tenant roles",
    "search.read": "Search tenant assets",
    "search.rebuild": "Rebuild tenant search projections",
    "search.index.activate": "Activate a tenant search index",
    "audit.read": "Read tenant audit events",
    **INVENTORY_PERMISSION_DEFINITIONS,
}

VIEWER_PERMISSIONS = {"assets.read", "assets.upload", "assets.delete", "search.read"}
OPERATOR_PERMISSIONS = VIEWER_PERMISSIONS | {
    "ai_operations.read",
    "ai_analysis.run",
    "ai_jobs.retry",
    "ai_jobs.cancel",
}
BILLING_ADMIN_PERMISSIONS = {
    "ai_operations.read",
    "ai_budget.read",
    "ai_budget.update",
}
SYSTEM_ROLE_DEFINITIONS = {
    "viewer": ("Viewer", "Read, search, upload, and delete files within assigned folders", VIEWER_PERMISSIONS),
    "operator": ("Operator", "Operate tenant AI processing", OPERATOR_PERMISSIONS),
    "tenant_admin": (
        "Tenant administrator",
        "All tenant-level permissions; never platform administration",
        set(PERMISSION_DEFINITIONS),
    ),
    "billing_admin": (
        "Billing administrator",
        "Read AI operations and manage tenant AI budgets",
        BILLING_ADMIN_PERMISSIONS,
    ),
}


def seed_tenant_rbac(session: Session, tenant_id: str) -> dict[str, int]:
    tenant = session.get(TenantModel, tenant_id)
    if tenant is None:
        raise LookupError("tenant not found")
    permissions_created = 0
    roles_created = 0
    assignments_created = 0
    permission_rows: dict[str, PermissionModel] = {}
    for key, description in PERMISSION_DEFINITIONS.items():
        row = session.scalar(select(PermissionModel).where(PermissionModel.permission_key == key))
        if row is None:
            row = PermissionModel(permission_key=key, description=description, status="active")
            session.add(row)
            session.flush()
            permissions_created += 1
        else:
            row.description = description
            row.status = "active"
        permission_rows[key] = row

    for role_key, (name, description, permission_keys) in SYSTEM_ROLE_DEFINITIONS.items():
        role = session.scalar(select(RoleModel).where(
            RoleModel.tenant_id == tenant_id,
            RoleModel.role_key == role_key,
        ))
        if role is None:
            role = RoleModel(
                tenant_id=tenant_id,
                role_key=role_key,
                name=name,
                description=description,
                is_system=True,
                protected=True,
                status="active",
            )
            session.add(role)
            session.flush()
            roles_created += 1
        else:
            role.name = name
            role.description = description
            role.is_system = True
            role.protected = True
            role.status = "active"
        existing = {
            item.permission_id: item
            for item in session.scalars(select(RolePermissionModel).where(RolePermissionModel.role_id == role.id))
        }
        desired_ids = {permission_rows[key].id for key in permission_keys}
        for permission_id in desired_ids - set(existing):
            session.add(RolePermissionModel(role_id=role.id, permission_id=permission_id))
            assignments_created += 1
        stale_ids = set(existing) - desired_ids
        if stale_ids:
            session.execute(delete(RolePermissionModel).where(
                RolePermissionModel.role_id == role.id,
                RolePermissionModel.permission_id.in_(stale_ids),
            ))
    session.flush()
    return {
        "permissions_created": permissions_created,
        "roles_created": roles_created,
        "role_permissions_created": assignments_created,
    }
