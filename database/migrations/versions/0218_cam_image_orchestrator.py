"""Add isolated CAM image state and shared renderer fence.

Revision ID: 0218_cam_image_orchestrator
Revises: 0217_listing_image_sms_color

Additive only. No existing image or video data is altered.
"""
from alembic import op
import sqlalchemy as sa

revision = "0218_cam_image_orchestrator"
down_revision = "0217_listing_image_sms_color"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_image_flow_controls",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("running", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "cam_image_batches",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("batch_id", sa.String(128), primary_key=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("pack_ids", sa.JSON(), nullable=False),
        sa.Column("release_decision", sa.JSON(), nullable=True),
    )
    op.create_table(
        "cam_image_packs",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("batch_id", sa.String(128), nullable=False),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("artwork_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("garment_sku", sa.String(10), nullable=False),
        sa.Column("pack_revision", sa.Integer(), nullable=False),
        sa.Column("placement_sha256", sa.String(64), nullable=False),
        sa.Column("skill_sha256", sa.String(64), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_state", sa.JSON(), nullable=True),
        sa.Column("wave_claims", sa.JSON(), nullable=False),
        sa.Column("ack_state", sa.JSON(), nullable=False),
        sa.Column("used_attempt_ids", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "listing_key", "artwork_sha256", "garment_sku",
                            "pack_revision", name="uq_cam_image_pack_identity"),
        sa.ForeignKeyConstraint(["tenant_id", "batch_id"],
                                ["cam_image_batches.tenant_id", "cam_image_batches.batch_id"],
                                name="fk_cam_image_pack_batch"),
        sa.CheckConstraint("pack_revision BETWEEN 1 AND 10000", name="ck_cam_image_pack_revision"),
        sa.CheckConstraint("epoch >= 0", name="ck_cam_image_pack_epoch"),
        sa.CheckConstraint("garment_sku IN ('C1717','G500','G180','G185')",
                           name="ck_cam_image_pack_sku"),
    )
    op.create_index("ix_cam_image_pack_batch", "cam_image_packs",
                    ["tenant_id", "batch_id", "pack_id"])
    op.create_table(
        "cam_image_roles",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("role", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_id", sa.String(128)),
        sa.Column("receipt_state", sa.JSON(), nullable=True),
        sa.Column("failure_code", sa.String(128)),
        sa.ForeignKeyConstraint(["tenant_id", "pack_id"],
                                ["cam_image_packs.tenant_id", "cam_image_packs.pack_id"],
                                name="fk_cam_image_role_pack"),
        sa.CheckConstraint("role BETWEEN 1 AND 13", name="ck_cam_image_role_number"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_cam_image_role_attempts"),
        sa.CheckConstraint("status IN ('pending','dispatch_uncertain','upload_pending',"
                           "'reconcile_required','verified','hold')",
                           name="ck_cam_image_role_status"),
    )
    op.create_index("ix_cam_image_role_status", "cam_image_roles",
                    ["tenant_id", "status", "pack_id"])
    op.create_table(
        "cam_render_resources",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("resource_id", sa.String(128), primary_key=True),
        sa.Column("integration_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("shared_with_video", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("owner_flow", sa.String(8)),
        sa.Column("owner_worker", sa.String(128)),
        sa.Column("owner_operation", sa.String(128)),
        sa.Column("expires_at", sa.BigInteger()),
        sa.CheckConstraint("epoch >= 0", name="ck_cam_render_resource_epoch"),
        sa.CheckConstraint("owner_flow IS NULL OR owner_flow IN ('image','video')",
                           name="ck_cam_render_resource_flow"),
    )


def downgrade():
    bind = op.get_bind()
    # A downgrade is safe only before admission. Never erase pack history.
    for table in ("cam_image_roles", "cam_image_packs", "cam_image_batches",
                  "cam_render_resources", "cam_image_flow_controls"):
        if bind.scalar(sa.text("SELECT COUNT(*) FROM " + table)):
            raise RuntimeError("Refusing to drop persisted Image Orchestrator state")
    op.drop_table("cam_render_resources")
    op.drop_index("ix_cam_image_role_status", table_name="cam_image_roles")
    op.drop_table("cam_image_roles")
    op.drop_index("ix_cam_image_pack_batch", table_name="cam_image_packs")
    op.drop_table("cam_image_packs")
    op.drop_table("cam_image_batches")
    op.drop_table("cam_image_flow_controls")