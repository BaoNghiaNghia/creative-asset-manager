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


def _sqlite_batch_recreate(*, downgrade: bool = False) -> None:
    """SQLite cannot ALTER COLUMN nullability or DROP a named CHECK in place.

    Recreate only the existing cam_muse_commands table, retaining its rows,
    composite FK/PK, unique key and index metadata. PostgreSQL continues to
    use the unchanged direct DDL path below.
    """
    if downgrade:
        actions = "action IN ('create_video','review_artwork')"
        phases = "phase IN ('review_artwork','create_video')"
        statuses = "status IN ('pending','claimed','receipt_submitted','reviewed','failed')"
    else:
        actions = "action IN ('create_video','review_artwork','execute_task')"
        phases = "phase IN ('review_artwork','create_video','execute_task')"
        statuses = "status IN ('pending','claimed','receipt_submitted','reviewed','failed','completed')"
    with op.batch_alter_table("cam_muse_commands", recreate="always") as batch:
        if downgrade:
            batch.drop_index("ix_cam_muse_command_ttl")
        batch.alter_column("listing_key", existing_type=sa.String(150),
                           nullable=not downgrade)
        batch.alter_column("artwork_sha256", existing_type=sa.String(64),
                           nullable=not downgrade)
        batch.drop_constraint("ck_cam_muse_command_action", type_="check")
        batch.drop_constraint("ck_cam_muse_command_phase", type_="check")
        batch.drop_constraint("ck_cam_muse_command_status", type_="check")
        batch.create_check_constraint("ck_cam_muse_command_action", actions)
        batch.create_check_constraint("ck_cam_muse_command_phase", phases)
        batch.create_check_constraint("ck_cam_muse_command_status", statuses)
        if not downgrade:
            batch.create_index("ix_cam_muse_command_ttl",
                               ["tenant_id", "action", "finished_at"])


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        _sqlite_batch_recreate()
        return
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
    if bind.dialect.name == "sqlite":
        _sqlite_batch_recreate(downgrade=True)
        return
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