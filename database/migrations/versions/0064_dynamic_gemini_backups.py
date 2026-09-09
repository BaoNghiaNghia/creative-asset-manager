from alembic import op

revision = "0064_dynamic_gemini_backups"
down_revision = "0063_gemini_backup_2_credential"
branch_labels = None
depends_on = None

_NEW = "provider IN ('gemini','gemini_video','gemini_image','gemini_backup_1','gemini_backup_2','gemini_backup_3','gemini_backup_4','gemini_backup_5','gemini_backup_6','gemini_backup_7','gemini_backup_8','gemini_backup_9','gemini_backup_10')"
_OLD = "provider IN ('gemini','gemini_video','gemini_image','gemini_backup','gemini_backup_2')"

def upgrade():
    # Existing configured backups retain their encrypted values and audit lineage.
    op.execute("UPDATE creative_ai_credentials SET provider = 'gemini_backup_1' WHERE provider = 'gemini_backup'")
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint("ck_creative_ai_credentials_provider", _NEW)

def downgrade():
    # Downgrade preserves the first two configured entries; extra backups must be removed deliberately.
    op.execute("UPDATE creative_ai_credentials SET provider = 'gemini_backup' WHERE provider = 'gemini_backup_1'")
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint("ck_creative_ai_credentials_provider", _OLD)
