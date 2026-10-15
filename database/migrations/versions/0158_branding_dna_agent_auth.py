"""Enroll dedicated Branding DNA agents without enabling browser scan.

Revision ID: 0158_branding_dna_agent_auth
Revises: 0157_branding_dna_foundation
"""
from alembic import op
import sqlalchemy as sa

revision = "0158_branding_dna_agent_auth"
down_revision = "0157_branding_dna_foundation"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("bc_browser_agents", sa.Column(
        "enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("bc_browser_agents", sa.Column("token_hash", sa.String(64)))
    op.add_column("bc_browser_agents", sa.Column("agent_version", sa.String(64)))

def downgrade() -> None:
    op.drop_column("bc_browser_agents", "agent_version")
    op.drop_column("bc_browser_agents", "token_hash")
    op.drop_column("bc_browser_agents", "enabled")
