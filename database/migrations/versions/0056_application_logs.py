"""Add tenant-scoped external application logs with ten-day retention.

Revision ID: 0056_application_logs
Revises: 0055_cloudflare_image_provider
"""
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0056_application_logs"
down_revision = "0055_cloudflare_image_provider"
branch_labels = None
depends_on = None

PERMISSION_KEY = "application_logs.manage"


def upgrade() -> None:
    op.create_table(
        "log_applications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("payload_schema_json", sa.JSON()),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_log_applications_tenant"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_log_applications_tenant_slug"),
        sa.UniqueConstraint("secret_hash", name="uq_log_applications_secret_hash"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_log_applications_tenant_id"),
    )
    op.create_index("ix_log_applications_tenant_active", "log_applications", ["tenant_id", "active"])
    op.create_table(
        "application_logs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("application_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("trace_id", sa.String(255)),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "application_id"], ["log_applications.tenant_id", "log_applications.id"], ondelete="CASCADE", name="fk_application_logs_tenant_application"),
        sa.UniqueConstraint("application_id", "idempotency_key", name="uq_application_logs_app_idempotency"),
        sa.CheckConstraint("level IN ('trace','debug','info','warning','error','critical')", name="ck_application_logs_level"),
    )
    op.create_index("ix_application_logs_app_received", "application_logs", ["tenant_id", "application_id", "received_at"])
    op.create_index("ix_application_logs_expires", "application_logs", ["expires_at"])
    op.create_index("ix_application_logs_trace", "application_logs", ["application_id", "trace_id"])

    bind = op.get_bind(); now = datetime.now(timezone.utc)
    permissions = sa.table("permissions", sa.column("id", sa.String), sa.column("permission_key", sa.String), sa.column("description", sa.Text), sa.column("status", sa.String), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)))
    roles = sa.table("roles", sa.column("id", sa.String), sa.column("role_key", sa.String))
    grants = sa.table("role_permissions", sa.column("id", sa.String), sa.column("role_id", sa.String), sa.column("permission_id", sa.String), sa.column("created_at", sa.DateTime(timezone=True)))
    permission_id = bind.scalar(sa.select(permissions.c.id).where(permissions.c.permission_key == PERMISSION_KEY))
    if permission_id is None:
        permission_id = str(uuid4())
        bind.execute(permissions.insert().values(id=permission_id, permission_key=PERMISSION_KEY, description="Manage external log applications", status="active", created_at=now, updated_at=now))
    role_ids = list(bind.scalars(sa.select(roles.c.id).where(roles.c.role_key == "tenant_admin")))
    assigned = set(bind.scalars(sa.select(grants.c.role_id).where(grants.c.permission_id == permission_id, grants.c.role_id.in_(role_ids)))) if role_ids else set()
    for role_id in role_ids:
        if role_id not in assigned: bind.execute(grants.insert().values(id=str(uuid4()), role_id=role_id, permission_id=permission_id, created_at=now))


def downgrade() -> None:
    op.drop_index("ix_application_logs_trace", table_name="application_logs")
    op.drop_index("ix_application_logs_expires", table_name="application_logs")
    op.drop_index("ix_application_logs_app_received", table_name="application_logs")
    op.drop_table("application_logs")
    op.drop_index("ix_log_applications_tenant_active", table_name="log_applications")
    op.drop_table("log_applications")
