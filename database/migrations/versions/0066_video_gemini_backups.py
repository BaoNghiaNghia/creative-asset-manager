"""add independent Video Gemini backup key pool

Revision ID: 0066_video_gemini_backups
Revises: 0065_pipeline_job_index
"""

from alembic import op

revision = "0066_video_gemini_backups"
down_revision = "0065_pipeline_job_index"
branch_labels = None
depends_on = None

_NEW = "provider IN ('gemini','gemini_video','gemini_image','gemini_backup_1','gemini_backup_2','gemini_backup_3','gemini_backup_4','gemini_backup_5','gemini_backup_6','gemini_backup_7','gemini_backup_8','gemini_backup_9','gemini_backup_10','gemini_video_backup_1','gemini_video_backup_2','gemini_video_backup_3','gemini_video_backup_4','gemini_video_backup_5','gemini_video_backup_6','gemini_video_backup_7','gemini_video_backup_8','gemini_video_backup_9','gemini_video_backup_10')"
_OLD = "provider IN ('gemini','gemini_video','gemini_image','gemini_backup_1','gemini_backup_2','gemini_backup_3','gemini_backup_4','gemini_backup_5','gemini_backup_6','gemini_backup_7','gemini_backup_8','gemini_backup_9','gemini_backup_10')"


def upgrade():
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint("ck_creative_ai_credentials_provider", _NEW)


def downgrade():
    configured = op.get_bind().exec_driver_sql(
        "SELECT COUNT(*) FROM creative_ai_credentials "
        "WHERE provider LIKE 'gemini_video_backup_%%'"
    ).scalar_one()
    if configured:
        raise RuntimeError(
            "Remove configured Video backup credentials before downgrading 0066."
        )
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint("ck_creative_ai_credentials_provider", _OLD)
