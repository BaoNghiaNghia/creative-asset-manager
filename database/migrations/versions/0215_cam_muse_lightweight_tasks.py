"""Generic, storage-bounded Muse tasks in existing cam_muse_commands table.

Revision ID: 0215_cam_muse_lightweight_tasks
Revises: 0214_cam_muse_command_bridge
"""
from alembic import op
import sqlalchemy as sa

revision = "0215_cam_muse_lightweight_tasks"
down_revision = "0214_cam_muse_command_bridge"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("cam_muse_commands", "listing_key",
                    existing_type=sa.String(150), nullable=True)
    op.alter_column("cam_muse_commands", "artwork_sha256",
                    existing_type=sa.String(64), nullable=True)
    op.drop_constraint("ck_cam_muse_command_action", "cam_muse_commands", type_="check")
    op.drop_constraint("ck_cam_muse_command_phase", "cam_muse_commands", type_="check")
    op.drop_constraint("ck_cam_muse_command_status", "cam_muse_commands", type_="check")
    op.create_check_constraint(
        "ck_cam_muse_command_action", "cam_muse_commands",
        "action IN ('create_video','review_artwork','execute_task')")
    op.create_check_constraint(
        "ck_cam_muse_command_phase", "cam_muse_commands",
        "phase IN ('review_artwork','create_video','execute_task')")
    op.create_check_constraint(
        "ck_cam_muse_command_status", "cam_muse_commands",
        "status IN ('pending','claimed','receipt_submitted','reviewed','failed','completed')")
    op.create_index("ix_cam_muse_command_ttl", "cam_muse_commands",
                    ["tenant_id", "action", "finished_at"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT COUNT(*) FROM cam_muse_commands WHERE action = 'execute_task' "
        "OR listing_key IS NULL OR artwork_sha256 IS NULL"
    )):
        raise RuntimeError("Refusing to delete independent Muse task history")
    op.drop_index("ix_cam_muse_command_ttl", table_name="cam_muse_commands")
    op.drop_constraint("ck_cam_muse_command_action", "cam_muse_commands", type_="check")
    op.drop_constraint("ck_cam_muse_command_phase", "cam_muse_commands", type_="check")
    op.drop_constraint("ck_cam_muse_command_status", "cam_muse_commands", type_="check")
    op.create_check_constraint("ck_cam_muse_command_action", "cam_muse_commands",
                               "action IN ('create_video','review_artwork')")
    op.create_check_constraint("ck_cam_muse_command_phase", "cam_muse_commands",
                               "phase IN ('review_artwork','create_video')")
    op.create_check_constraint("ck_cam_muse_command_status", "cam_muse_commands",
                               "status IN ('pending','claimed','receipt_submitted','reviewed','failed')")
    op.alter_column("cam_muse_commands", "listing_key",
                    existing_type=sa.String(150), nullable=False)
    op.alter_column("cam_muse_commands", "artwork_sha256",
                    existing_type=sa.String(64), nullable=False)
