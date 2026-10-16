"""BD-01 append-only listing/gallery evidence (no automated scanning).

Revision ID: 0159_branding_dna_evidence
Revises: 0158_branding_dna_agent_auth
"""
from alembic import op
import sqlalchemy as sa

revision = "0159_branding_dna_evidence"
down_revision = "0158_branding_dna_agent_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bc_listing_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), sa.ForeignKey("bc_shops.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("listing_key", sa.String(255), nullable=False),
        sa.Column("source_revision", sa.String(128), nullable=False),
        sa.Column("export_reference", sa.String(128), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("evidence_state", sa.String(32), nullable=False, server_default="imported_unverified"),
        sa.Column("coverage_state", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", "shop_id", "listing_key", "source_revision",
                            name="uq_bc_listing_snapshot_revision"),
        sa.UniqueConstraint("tenant_id", "shop_id", "id", name="uq_bc_listing_snapshot_scope"),
    )
    op.create_index("ix_bc_listing_snapshots_shop_date", "bc_listing_snapshots",
                    ["tenant_id", "shop_id", "captured_at"])
    op.create_table(
        "bc_listing_images",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("shop_id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("asset_sha256", sa.String(64), nullable=False),
        sa.Column("source_url_sha256", sa.String(64), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("variant_key", sa.String(128)),
        sa.Column("rights_state", sa.String(24), nullable=False, server_default="unknown"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shop_id", "snapshot_id"],
            ["bc_listing_snapshots.tenant_id", "bc_listing_snapshots.shop_id", "bc_listing_snapshots.id"],
            name="fk_bc_listing_images_snapshot_scope", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("snapshot_id", "sequence_index", name="uq_bc_listing_image_gallery_order"),
    )
    op.create_index("ix_bc_listing_images_snapshot_order", "bc_listing_images",
                    ["snapshot_id", "sequence_index"])


def downgrade() -> None:
    # Destructive downgrade is intentionally explicit: use feature disablement
    # and retention review before applying to any environment with real evidence.
    op.drop_index("ix_bc_listing_images_snapshot_order", table_name="bc_listing_images")
    op.drop_table("bc_listing_images")
    op.drop_index("ix_bc_listing_snapshots_shop_date", table_name="bc_listing_snapshots")
    op.drop_table("bc_listing_snapshots")
