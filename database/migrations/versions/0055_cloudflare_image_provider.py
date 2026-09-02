"""Add Cloudflare SD image generation provider.

Revision ID: 0055_cloudflare_image_provider
Revises: 0054_image_generation_runtime
"""
from alembic import op

revision = "0055_cloudflare_image_provider"
down_revision = "0054_image_generation_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_generation_runs") as batch_op:
        batch_op.drop_constraint("ck_image_generation_runs_provider", type_="check")
        batch_op.create_check_constraint(
            "ck_image_generation_runs_provider",
            "provider IN ('adobe_firefly', 'cloudflare_sd', 'gemini')",
        )


def downgrade() -> None:
    with op.batch_alter_table("image_generation_runs") as batch_op:
        batch_op.drop_constraint("ck_image_generation_runs_provider", type_="check")
        batch_op.create_check_constraint(
            "ck_image_generation_runs_provider",
            "provider IN ('adobe_firefly', 'gemini')",
        )
