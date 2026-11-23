"""Command bridge for ChatGPT -> CodeLocal -> CAM -> Muse.

Revision ID: 0214_cam_muse_command_bridge
Revises: 0213_cam_muse_bridge
"""
from alembic import op
import sqlalchemy as sa

revision = "0214_cam_muse_command_bridge"
down_revision = "0213_cam_muse_bridge"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cam_muse_commands",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("listing_key", sa.String(150), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("artwork_sha256", sa.String(64), nullable=False),
        sa.Column("lease_hash", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "listing_key"],
            ["cam_muse_jobs.tenant_id", "cam_muse_jobs.listing_key"],
            name="fk_cam_muse_command_listing", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "request_id",
                            name="uq_cam_muse_command_request_id"),
        sa.CheckConstraint("action IN ('create_video','review_artwork')",
                           name="ck_cam_muse_command_action"),
        sa.CheckConstraint("status IN ('pending','claimed','receipt_submitted','reviewed','failed')",
                           name="ck_cam_muse_command_status"),
        sa.CheckConstraint("phase IN ('review_artwork','create_video')",
                           name="ck_cam_muse_command_phase"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 5",
                           name="ck_cam_muse_command_attempts"),
    )
    op.create_index("ix_cam_muse_command_pending", "cam_muse_commands",
                    ["tenant_id", "status", "created_at"])


def downgrade():
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT COUNT(*) FROM cam_muse_commands")):
        raise RuntimeError("Refusing to destroy Muse command history")
    op.drop_index("ix_cam_muse_command_pending", table_name="cam_muse_commands")
    op.drop_table("cam_muse_commands")
