"""Store the active public-review share secret encrypted.

Revision ID: 0088_public_review_encrypted_secret
Revises: 0087_inventory_audit_knowledge
"""
from datetime import datetime, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "0088_public_review_encrypted_secret"
down_revision = "0087_inventory_audit_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "public_shares",
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
    )
    op.add_column(
        "public_shares",
        sa.Column("secret_key_version", sa.String(64), nullable=True),
    )
    minimum_expiry = datetime.now(timezone.utc) + timedelta(days=7)
    op.execute(
        sa.text(
            """
            UPDATE public_shares
            SET expires_at = :minimum_expiry
            WHERE status = 'active'
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at < :minimum_expiry)
            """
        ).bindparams(minimum_expiry=minimum_expiry)
    )


def downgrade():
    op.drop_column("public_shares", "secret_key_version")
    op.drop_column("public_shares", "secret_ciphertext")
