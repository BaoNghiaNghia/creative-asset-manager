"""Add Realistic Review UGC Pinterest scout foundation.

Revision ID: 0093_realistic_review_ugc
Revises: 0092_asset_source_link_invariant
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0093_realistic_review_ugc"
down_revision = "0092_asset_source_link_invariant"
branch_labels = None
depends_on = None

_PERMISSION_DEFINITIONS = {
    "realistic_review_ugc.read": "Read Realistic Review UGC workflows",
    "realistic_review_ugc.run": "Run Realistic Review UGC workflows",
    "realistic_review_ugc.configure": "Configure Realistic Review UGC workflows",
}


def _seed_permissions(bind) -> None:
    now = datetime.now(timezone.utc)
    permission_ids: dict[str, str] = {}
    for key, description in _PERMISSION_DEFINITIONS.items():
        existing = bind.execute(
            sa.text("SELECT id FROM permissions WHERE permission_key = :key"),
            {"key": key},
        ).scalar()
        if existing:
            permission_ids[key] = str(existing)
            bind.execute(
                sa.text(
                    "UPDATE permissions SET description = :description, status = 'active', "
                    "updated_at = :now WHERE id = :id"
                ),
                {"description": description, "now": now, "id": existing},
            )
            continue
        permission_id = str(uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO permissions "
                "(id, permission_key, description, status, created_at, updated_at) "
                "VALUES (:id, :key, :description, 'active', :now, :now)"
            ),
            {
                "id": permission_id,
                "key": key,
                "description": description,
                "now": now,
            },
        )
        permission_ids[key] = permission_id

    roles = bind.execute(
        sa.text(
            "SELECT id, role_key FROM roles "
            "WHERE status = 'active' AND role_key IN ('operator', 'tenant_admin')"
        )
    ).mappings().all()
    for role in roles:
        keys = (
            tuple(_PERMISSION_DEFINITIONS)
            if role["role_key"] == "tenant_admin"
            else ("realistic_review_ugc.read", "realistic_review_ugc.run")
        )
        for key in keys:
            exists = bind.execute(
                sa.text(
                    "SELECT 1 FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id"
                ),
                {
                    "role_id": role["id"],
                    "permission_id": permission_ids[key],
                },
            ).scalar()
            if exists:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (id, role_id, permission_id, created_at) "
                    "VALUES (:id, :role_id, :permission_id, :now)"
                ),
                {
                    "id": str(uuid4()),
                    "role_id": role["id"],
                    "permission_id": permission_ids[key],
                    "now": now,
                },
            )


def upgrade() -> None:
    op.create_table(
        "rrugc_campaigns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("query", sa.String(length=500), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_scroll_batches", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("auto_import", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("scout_token_hash", sa.String(length=64), nullable=False),
        sa.Column("scout_status", sa.String(length=32), nullable=False, server_default="offline"),
        sa.Column("scout_last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rrugc_campaign_tenant_id"),
    )
    op.create_index(
        "ix_rrugc_campaign_tenant_status",
        "rrugc_campaigns",
        ["tenant_id", "status", "updated_at"],
    )

    op.create_table(
        "rrugc_candidates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("pin_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("alt_text", sa.Text()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="discovered"),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("image_format", sa.String(length=16)),
        sa.Column("remote_file_id", sa.String(length=255)),
        sa.Column("remote_folder_id", sa.String(length=255)),
        sa.Column("web_url", sa.Text()),
        sa.Column("last_error_code", sa.String(length=100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_candidate_campaign",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "campaign_id", "source_key",
            name="uq_rrugc_candidate_source",
        ),
    )
    op.create_index(
        "ix_rrugc_candidate_campaign_status",
        "rrugc_candidates",
        ["tenant_id", "campaign_id", "status", "created_at"],
    )
    op.create_index(
        "ix_rrugc_candidate_content_hash",
        "rrugc_candidates",
        ["tenant_id", "content_hash"],
    )

    _seed_permissions(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    permission_ids = bind.execute(
        sa.text(
            "SELECT id FROM permissions "
            "WHERE permission_key IN "
            "('realistic_review_ugc.read','realistic_review_ugc.run','realistic_review_ugc.configure')"
        )
    ).scalars().all()
    for permission_id in permission_ids:
        bind.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :permission_id"),
            {"permission_id": permission_id},
        )
        bind.execute(
            sa.text("DELETE FROM permissions WHERE id = :permission_id"),
            {"permission_id": permission_id},
        )
    op.drop_index("ix_rrugc_candidate_content_hash", table_name="rrugc_candidates")
    op.drop_index("ix_rrugc_candidate_campaign_status", table_name="rrugc_candidates")
    op.drop_table("rrugc_candidates")
    op.drop_index("ix_rrugc_campaign_tenant_status", table_name="rrugc_campaigns")
    op.drop_table("rrugc_campaigns")
