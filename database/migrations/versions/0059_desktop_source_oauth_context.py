"""add desktop source OAuth handoff context

Revision ID: 0059_desktop_source_ctx
Revises: 0058_desktop_oauth_handoff
"""
from alembic import op
import sqlalchemy as sa

revision = "0059_desktop_source_ctx"
down_revision = "0058_desktop_oauth_handoff"
branch_labels = None
depends_on = None

def upgrade():
    # SQLite cannot ALTER a table to add a check constraint. Alembic batch
    # mode transparently rebuilds it there while retaining regular ALTERs on
    # PostgreSQL, so development startup and production use one migration.
    with op.batch_alter_table("desktop_oauth_handoffs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "intent",
                sa.String(length=32),
                nullable=False,
                server_default="application_login",
            )
        )
        batch_op.add_column(
            sa.Column("initiating_user_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("initiating_tenant_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "reconnect_external_source_id", sa.String(length=36), nullable=True
            )
        )
        batch_op.create_check_constraint(
            "ck_desktop_oauth_handoffs_intent",
            "intent IN ('application_login','google_drive_connect','onedrive_connect')",
        )

def downgrade():
    with op.batch_alter_table("desktop_oauth_handoffs") as batch_op:
        batch_op.drop_constraint("ck_desktop_oauth_handoffs_intent", type_="check")
        batch_op.drop_column("reconnect_external_source_id")
        batch_op.drop_column("initiating_tenant_id")
        batch_op.drop_column("initiating_user_id")
        batch_op.drop_column("intent")
