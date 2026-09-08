"""Allow encrypted Gemini backup credentials.

Revision ID: 0062_gemini_backup_credential
Revises: 0061_tenant_job_priorities
"""
from alembic import op
import sqlalchemy as sa
revision = "0062_gemini_backup_credential"
down_revision = "0061_tenant_job_priorities"
branch_labels = None
depends_on = None
def upgrade():
    with op.batch_alter_table("creative_ai_credentials") as batch_op:
        batch_op.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch_op.create_check_constraint("ck_creative_ai_credentials_provider", "provider IN ('gemini', 'gemini_video', 'gemini_image', 'gemini_backup')")
def downgrade():
    with op.batch_alter_table("creative_ai_credentials") as batch_op:
        batch_op.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch_op.create_check_constraint("ck_creative_ai_credentials_provider", "provider IN ('gemini', 'gemini_video', 'gemini_image')")
