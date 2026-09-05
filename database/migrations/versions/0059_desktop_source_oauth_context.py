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
    op.add_column("desktop_oauth_handoffs", sa.Column("intent", sa.String(length=32), nullable=False, server_default="application_login"))
    op.add_column("desktop_oauth_handoffs", sa.Column("initiating_user_id", sa.String(length=36), nullable=True))
    op.add_column("desktop_oauth_handoffs", sa.Column("initiating_tenant_id", sa.String(length=255), nullable=True))
    op.add_column("desktop_oauth_handoffs", sa.Column("reconnect_external_source_id", sa.String(length=36), nullable=True))
    op.create_check_constraint("ck_desktop_oauth_handoffs_intent", "desktop_oauth_handoffs", "intent IN ('application_login','google_drive_connect','onedrive_connect')")

def downgrade():
    op.drop_constraint("ck_desktop_oauth_handoffs_intent", "desktop_oauth_handoffs", type_="check")
    op.drop_column("desktop_oauth_handoffs", "reconnect_external_source_id")
    op.drop_column("desktop_oauth_handoffs", "initiating_tenant_id")
    op.drop_column("desktop_oauth_handoffs", "initiating_user_id")
    op.drop_column("desktop_oauth_handoffs", "intent")
