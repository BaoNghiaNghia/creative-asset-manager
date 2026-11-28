"""Independent image commands, capability policy and upload intent journal.

Revision ID: 0219_cam_image_commands
Revises: 0218_cam_image_orchestrator
"""
from alembic import op
import sqlalchemy as sa

revision = "0219_cam_image_commands"
down_revision = "0218_cam_image_orchestrator"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cam_image_packs", sa.Column("approved_plan", sa.JSON(), nullable=True))
    op.create_table(
        "cam_image_commands",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("pack_id", sa.String(64), nullable=False),
        sa.Column("wave", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("claim_epoch", sa.Integer()),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("resource_epoch", sa.Integer()),
        sa.Column("image_granted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["tenant_id","pack_id"],
            ["cam_image_packs.tenant_id","cam_image_packs.pack_id"], name="fk_cam_image_command_pack"),
        sa.UniqueConstraint("tenant_id","pack_id","wave",name="uq_cam_image_command_wave"),
        sa.CheckConstraint("wave IN (1,2)",name="ck_cam_image_command_wave"),
        sa.CheckConstraint("status IN ('pending','claimed','completed','hold')",
                           name="ck_cam_image_command_status"),
    )
    op.create_index("ix_cam_image_command_status","cam_image_commands",
                    ["tenant_id","status","command_id"])
    op.create_table(
        "cam_image_capability_states",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("worker_id", sa.String(128), primary_key=True),
        sa.Column("claim_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skill_sha256", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("cooldown_until", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "cam_image_upload_intents",
        sa.Column("tenant_id", sa.String(255), primary_key=True),
        sa.Column("pack_id", sa.String(64), primary_key=True),
        sa.Column("role", sa.Integer(), primary_key=True),
        sa.Column("attempt_id", sa.String(128), primary_key=True),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("qa_receipt_id", sa.String(128), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.ForeignKeyConstraint(["tenant_id","pack_id","role"],
            ["cam_image_roles.tenant_id","cam_image_roles.pack_id","cam_image_roles.role"],
            name="fk_cam_image_upload_role"),
        sa.CheckConstraint("width BETWEEN 1600 AND 4096 AND width = height",
                           name="ck_cam_image_upload_dimensions"),
        sa.CheckConstraint("status IN ('pending','verified','reconcile_required')",
                           name="ck_cam_image_upload_status"),
    )


def downgrade():
    db = op.get_bind()
    for name in ("cam_image_upload_intents","cam_image_capability_states","cam_image_commands"):
        if db.scalar(sa.text("SELECT COUNT(*) FROM " + name)):
            raise RuntimeError("Refusing to discard image command history")
    if db.scalar(sa.text("SELECT COUNT(*) FROM cam_image_packs WHERE approved_plan IS NOT NULL")):
        raise RuntimeError("Refusing to discard approved image plans")
    op.drop_table("cam_image_upload_intents")
    op.drop_table("cam_image_capability_states")
    op.drop_index("ix_cam_image_command_status",table_name="cam_image_commands")
    op.drop_table("cam_image_commands")
    op.drop_column("cam_image_packs","approved_plan")