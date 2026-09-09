from alembic import op
revision = "0063_gemini_backup_2_credential"
down_revision = "0062_gemini_backup_credential"
branch_labels = None
depends_on = None
def upgrade():
 with op.batch_alter_table("creative_ai_credentials") as b:
  b.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
  b.create_check_constraint("ck_creative_ai_credentials_provider", "provider IN ('gemini','gemini_video','gemini_image','gemini_backup','gemini_backup_2')")
def downgrade():
 with op.batch_alter_table("creative_ai_credentials") as b:
  b.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
  b.create_check_constraint("ck_creative_ai_credentials_provider", "provider IN ('gemini','gemini_video','gemini_image','gemini_backup')")
