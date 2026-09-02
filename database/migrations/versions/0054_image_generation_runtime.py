"""Complete image generation state and authorization.

Revision ID: 0054_image_generation_runtime
Revises: 0053_image_generation_runs
"""
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0054_image_generation_runtime"
down_revision = "0053_image_generation_runs"
branch_labels = None
depends_on = None

PERMISSION_KEY = "assets.generate"
PERMISSION_DESCRIPTION = "Generate derived tenant assets"


def upgrade() -> None:
    op.drop_constraint("ck_image_generation_runs_status", "image_generation_runs", type_="check")
    op.create_check_constraint(
        "ck_image_generation_runs_status",
        "image_generation_runs",
        "status IN ('queued', 'preparing', 'submitted', 'running', 'storing', 'completed', 'failed', 'cancelled')",
    )
    for name in ("left", "top", "right", "bottom"):
        op.add_column(
            "image_generation_runs",
            sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    op.create_foreign_key(
        "fk_image_generation_runs_source_source_asset",
        "image_generation_runs",
        "source_assets",
        ["tenant_id", "source_source_asset_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_creative_ai_credentials_provider", "creative_ai_credentials", type_="check")
    op.create_check_constraint(
        "ck_creative_ai_credentials_provider",
        "creative_ai_credentials",
        "provider IN ('gemini', 'gemini_video', 'gemini_image')",
    )

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
    grants = sa.table(
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
        bind.execute(
            permissions.insert().values(
                id=permission_id,
                permission_key=PERMISSION_KEY,
                description=PERMISSION_DESCRIPTION,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
    role_ids = list(
        bind.scalars(sa.select(roles.c.id).where(roles.c.role_key.in_(("operator", "tenant_admin"))))
    )
    assigned = (
        set(
            bind.scalars(
                sa.select(grants.c.role_id).where(
                    grants.c.permission_id == permission_id,
                    grants.c.role_id.in_(role_ids),
                )
            )
        )
        if role_ids
        else set()
    )
    for role_id in role_ids:
        if role_id not in assigned:
            bind.execute(
                grants.insert().values(
                    id=str(uuid4()),
                    role_id=role_id,
                    permission_id=permission_id,
                    created_at=now,
                )
            )


def downgrade() -> None:
    # Preserve the expanded status/credential constraints and permission so a
    # rollback cannot strand cancelled runs or encrypted gemini_image rows.
    op.drop_constraint(
        "fk_image_generation_runs_source_source_asset",
        "image_generation_runs",
        type_="foreignkey",
    )
    for name in ("bottom", "right", "top", "left"):
        op.drop_column("image_generation_runs", name)
