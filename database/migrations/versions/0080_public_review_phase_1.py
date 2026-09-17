"""add Public Review durable data foundation

Revision ID: 0080_public_review_phase_1
Revises: 0079_cp_branch_versions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0080_public_review_phase_1"
down_revision = "0079_cp_branch_versions"
branch_labels = None
depends_on = None

JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table("public_shares",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("public_id", sa.String(64), nullable=False), sa.Column("tenant_id", sa.String(255), nullable=False), sa.Column("name", sa.String(255), nullable=False), sa.Column("secret_digest", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="active"), sa.Column("allow_comments", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("allow_download", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("created_by", sa.String(512), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE", name="fk_public_shares_tenant_id"), sa.UniqueConstraint("public_id", name="uq_public_shares_public_id"), sa.UniqueConstraint("tenant_id", "id", name="uq_public_shares_tenant_id"), sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_public_shares_status"), sa.CheckConstraint("length(trim(name)) BETWEEN 1 AND 255", name="ck_public_shares_name"), sa.CheckConstraint("length(secret_digest) = 64", name="ck_public_shares_secret_digest"))
    op.create_index("ix_public_shares_tenant_status", "public_shares", ["tenant_id", "status", "updated_at"])
    op.create_index("ix_public_shares_secret_digest", "public_shares", ["secret_digest"])
    op.create_table("public_share_scopes",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(255), nullable=False), sa.Column("share_id", sa.String(36), nullable=False), sa.Column("external_source_id", sa.String(36), nullable=False), sa.Column("folder_external_id", sa.String(2048), nullable=False), sa.Column("folder_name", sa.String(1024)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_public_share_scopes_tenant_share"), sa.ForeignKeyConstraint(["tenant_id", "external_source_id"], ["external_sources.tenant_id", "external_sources.id"], ondelete="RESTRICT", name="fk_public_share_scopes_tenant_source"), sa.UniqueConstraint("share_id", "external_source_id", "folder_external_id", name="uq_public_share_scopes_identity"))
    op.create_index("ix_public_share_scopes_tenant_share", "public_share_scopes", ["tenant_id", "share_id"])
    op.create_table("public_share_guests",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(255), nullable=False), sa.Column("share_id", sa.String(36), nullable=False), sa.Column("display_name", sa.String(160), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_public_share_guests_tenant_share"), sa.UniqueConstraint("tenant_id", "share_id", "id", name="uq_public_share_guests_tenant_share_id"), sa.CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 160", name="ck_public_share_guests_display_name"))
    op.create_index("ix_public_share_guests_tenant_share", "public_share_guests", ["tenant_id", "share_id"])
    op.create_table("public_share_sessions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(255), nullable=False), sa.Column("share_id", sa.String(36), nullable=False), sa.Column("session_digest", sa.String(64), nullable=False), sa.Column("guest_id", sa.String(36)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_public_share_sessions_tenant_share"), sa.ForeignKeyConstraint(["tenant_id", "share_id", "guest_id"], ["public_share_guests.tenant_id", "public_share_guests.share_id", "public_share_guests.id"], ondelete="RESTRICT", name="fk_public_share_sessions_tenant_share_guest"), sa.UniqueConstraint("session_digest", name="uq_public_share_sessions_digest"), sa.UniqueConstraint("tenant_id", "share_id", "id", name="uq_public_share_sessions_tenant_share_id"), sa.CheckConstraint("length(session_digest) = 64", name="ck_public_share_sessions_digest"))
    op.create_index("ix_public_share_sessions_tenant_share", "public_share_sessions", ["tenant_id", "share_id", "expires_at"])
    op.create_index("ix_public_share_sessions_active", "public_share_sessions", ["expires_at", "revoked_at"])
    op.create_table("asset_annotations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(255), nullable=False), sa.Column("share_id", sa.String(36), nullable=False), sa.Column("asset_id", sa.String(36), nullable=False), sa.Column("source_asset_id", sa.String(36), nullable=False), sa.Column("guest_id", sa.String(36), nullable=False), sa.Column("parent_annotation_id", sa.String(36)), sa.Column("anchor_x", sa.Float()), sa.Column("anchor_y", sa.Float()), sa.Column("content_json", JSON_DOCUMENT, nullable=False), sa.Column("plain_text", sa.Text(), nullable=False), sa.Column("status", sa.String(16), nullable=False, server_default="open"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("edited_at", sa.DateTime(timezone=True)), sa.Column("resolved_at", sa.DateTime(timezone=True)), sa.Column("resolved_by", sa.String(512)), sa.ForeignKeyConstraint(["tenant_id", "share_id"], ["public_shares.tenant_id", "public_shares.id"], ondelete="CASCADE", name="fk_asset_annotations_tenant_share"), sa.ForeignKeyConstraint(["tenant_id", "asset_id"], ["assets.tenant_id", "assets.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_asset"), sa.ForeignKeyConstraint(["tenant_id", "source_asset_id"], ["source_assets.tenant_id", "source_assets.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_source_asset"), sa.ForeignKeyConstraint(["tenant_id", "share_id", "guest_id"], ["public_share_guests.tenant_id", "public_share_guests.share_id", "public_share_guests.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_share_guest"), sa.ForeignKeyConstraint(["tenant_id", "share_id", "parent_annotation_id"], ["asset_annotations.tenant_id", "asset_annotations.share_id", "asset_annotations.id"], ondelete="RESTRICT", name="fk_asset_annotations_tenant_share_parent"), sa.UniqueConstraint("tenant_id", "share_id", "id", name="uq_asset_annotations_tenant_share_id"), sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_asset_annotations_status"), sa.CheckConstraint("(anchor_x IS NULL AND anchor_y IS NULL) OR (anchor_x >= 0 AND anchor_x <= 1 AND anchor_y >= 0 AND anchor_y <= 1)", name="ck_asset_annotations_anchor_pair"), sa.CheckConstraint("length(plain_text) <= 10000", name="ck_asset_annotations_plain_text"))
    op.create_index("ix_asset_annotations_tenant_share_asset", "asset_annotations", ["tenant_id", "share_id", "asset_id", "source_asset_id", "created_at"])
    op.create_index("ix_asset_annotations_tenant_share_status", "asset_annotations", ["tenant_id", "share_id", "status", "updated_at"])


def downgrade():
    for index, table in (("ix_asset_annotations_tenant_share_status", "asset_annotations"), ("ix_asset_annotations_tenant_share_asset", "asset_annotations"), ("ix_public_share_sessions_active", "public_share_sessions"), ("ix_public_share_sessions_tenant_share", "public_share_sessions"), ("ix_public_share_guests_tenant_share", "public_share_guests"), ("ix_public_share_scopes_tenant_share", "public_share_scopes"), ("ix_public_shares_secret_digest", "public_shares"), ("ix_public_shares_tenant_status", "public_shares")):
        op.drop_index(index, table_name=table)
    op.drop_table("asset_annotations")
    op.drop_table("public_share_sessions")
    op.drop_table("public_share_guests")
    op.drop_table("public_share_scopes")
    op.drop_table("public_shares")
