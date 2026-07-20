"""Persistent encrypted OAuth tokens and distributed sessions.

Revision ID: 0013_persistent_oauth_sessions
Revises: 0012_ai_batch_processing

Rollback requires disabling provider login, revoking/draining active sessions,
and accepting that encrypted connection/session/state records are removed.
"""
from alembic import op
import sqlalchemy as sa

revision="0013_persistent_oauth_sessions"
down_revision="0012_ai_batch_processing"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        "oauth_connections",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("tenant_id",sa.String(255),nullable=False),
        sa.Column("provider",sa.String(32),nullable=False),
        sa.Column("provider_account_id",sa.String(512),nullable=False),
        sa.Column("account_email",sa.String(512)),
        sa.Column("access_token_ciphertext",sa.Text()),
        sa.Column("refresh_token_ciphertext",sa.Text()),
        sa.Column("access_token_expires_at",sa.DateTime(timezone=True)),
        sa.Column("scopes_json",sa.JSON(),nullable=False),
        sa.Column("token_type",sa.String(50)),
        sa.Column("key_version",sa.String(64),nullable=False),
        sa.Column("provider_metadata_json",sa.JSON(),nullable=False),
        sa.Column("status",sa.String(32),nullable=False),
        sa.Column("last_refresh_at",sa.DateTime(timezone=True)),
        sa.Column("refresh_error_json",sa.JSON()),
        sa.Column("refresh_claimed_by",sa.String(255)),
        sa.Column("refresh_lease_expires_at",sa.DateTime(timezone=True)),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("revoked_at",sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id","provider","provider_account_id",name="uq_oauth_connections_identity"),
        sa.UniqueConstraint("tenant_id","id",name="uq_oauth_connections_tenant_id"),
        sa.UniqueConstraint("tenant_id","id","provider",name="uq_oauth_connections_tenant_id_provider"),
        sa.CheckConstraint("status IN ('active','refresh_error','reconnect_required','revoked')",name="ck_oauth_connections_status"),
    )
    op.create_index("ix_oauth_connections_refresh","oauth_connections",["status","access_token_expires_at"])
    op.create_table(
        "auth_sessions",
        sa.Column("session_id_hash",sa.String(64),primary_key=True),
        sa.Column("tenant_id",sa.String(255),nullable=False),
        sa.Column("provider",sa.String(32),nullable=False),
        sa.Column("connection_id",sa.String(36),nullable=False),
        sa.Column("user_json",sa.JSON(),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("last_seen_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("revoked_at",sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id","connection_id","provider"],["oauth_connections.tenant_id","oauth_connections.id","oauth_connections.provider"],ondelete="CASCADE",name="fk_auth_sessions_tenant_connection_provider"),
    )
    op.create_index("ix_auth_sessions_expiry","auth_sessions",["expires_at","revoked_at"])
    op.create_index("ix_auth_sessions_tenant_provider","auth_sessions",["tenant_id","provider"])
    op.create_table(
        "oauth_transactions",
        sa.Column("state_hash",sa.String(64),primary_key=True),
        sa.Column("provider",sa.String(32),nullable=False),
        sa.Column("session_binding_hash",sa.String(64)),
        sa.Column("redirect_intent",sa.String(1024),nullable=False),
        sa.Column("code_verifier_ciphertext",sa.Text()),
        sa.Column("key_version",sa.String(64),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("consumed_at",sa.DateTime(timezone=True)),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
    )
    op.create_index("ix_oauth_transactions_expiry","oauth_transactions",["expires_at","consumed_at"])
    op.create_table(
        "auth_audit_events",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("tenant_id",sa.String(255)),
        sa.Column("actor_id",sa.String(512)),
        sa.Column("provider",sa.String(32)),
        sa.Column("connection_id",sa.String(36)),
        sa.Column("session_id_hash",sa.String(64)),
        sa.Column("action",sa.String(64),nullable=False),
        sa.Column("detail_json",sa.JSON(),nullable=False),
        sa.Column("occurred_at",sa.DateTime(timezone=True),nullable=False),
    )
    op.create_index("ix_auth_audit_tenant_time","auth_audit_events",["tenant_id","occurred_at"])

def downgrade():
    op.drop_index("ix_auth_audit_tenant_time",table_name="auth_audit_events"); op.drop_table("auth_audit_events")
    op.drop_index("ix_oauth_transactions_expiry",table_name="oauth_transactions"); op.drop_table("oauth_transactions")
    op.drop_index("ix_auth_sessions_tenant_provider",table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expiry",table_name="auth_sessions"); op.drop_table("auth_sessions")
    op.drop_index("ix_oauth_connections_refresh",table_name="oauth_connections"); op.drop_table("oauth_connections")
