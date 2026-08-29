"""Allow a separately encrypted Gemini credential for Video AI.

Revision ID: 0052_video_gemini_credential
Revises: 0051_inventory_material_registry
"""

from alembic import op


revision = "0052_video_gemini_credential"
down_revision = "0051_inventory_material_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint(
            "ck_creative_ai_credentials_provider",
            "provider IN ('gemini', 'gemini_video')",
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM creative_ai_credentials WHERE provider = 'gemini_video'"
    )
    with op.batch_alter_table("creative_ai_credentials") as batch:
        batch.drop_constraint("ck_creative_ai_credentials_provider", type_="check")
        batch.create_check_constraint(
            "ck_creative_ai_credentials_provider", "provider = 'gemini'"
