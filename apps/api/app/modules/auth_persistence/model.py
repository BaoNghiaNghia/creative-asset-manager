from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','disabled')", name="ck_users_status"),
        Index("ix_users_primary_email", "primary_email"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    primary_email: Mapped[str | None] = mapped_column(String(512))
    display_name: Mapped[str | None] = mapped_column(String(512))
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class UserIdentityModel(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_user_identities_provider_subject"),
        CheckConstraint("provider IN ('google','microsoft')", name="ck_user_identities_provider"),
        CheckConstraint("length(trim(provider_subject)) > 0", name="ck_user_identities_subject_not_empty"),
        Index("ix_user_identities_user_id", "user_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    provider_email: Mapped[str | None] = mapped_column(String(512))
    provider_tenant_id: Mapped[str | None] = mapped_column(String(512))
    provider_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class TenantModel(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
        CheckConstraint("status IN ('active','suspended','disabled')", name="ck_tenants_status"),
    )
    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

class TenantMembershipModel(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_memberships_tenant_id_id"),
        CheckConstraint("status IN ('invited','active','suspended','removed')", name="ck_tenant_memberships_status"),
        Index("ix_tenant_memberships_user_status", "user_id", "status"),
        Index("ix_tenant_memberships_tenant_status", "tenant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="invited")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

class OAuthConnectionModel(Base):
    __tablename__ = "oauth_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_account_id", name="uq_oauth_connections_identity"),
        UniqueConstraint("tenant_id", "id", name="uq_oauth_connections_tenant_id"),
        UniqueConstraint("tenant_id", "id", "provider", name="uq_oauth_connections_tenant_id_provider"),
        CheckConstraint("status IN ('active','refresh_error','reconnect_required','revoked')", name="ck_oauth_connections_status"),
        Index("ix_oauth_connections_refresh", "status", "access_token_expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(512), nullable=False)
    account_email: Mapped[str | None] = mapped_column(String(512))
    access_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_token_ciphertext: Mapped[str | None] = mapped_column(Text)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    token_type: Mapped[str | None] = mapped_column(String(50))
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_error_json: Mapped[dict | None] = mapped_column(JSON)
    refresh_claimed_by: Mapped[str | None] = mapped_column(String(255))
    refresh_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class AuthSessionModel(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id", "provider"],
            ["oauth_connections.tenant_id", "oauth_connections.id", "oauth_connections.provider"],
            ondelete="CASCADE", name="fk_auth_sessions_tenant_connection_provider",
        ),
        Index("ix_auth_sessions_expiry", "expires_at", "revoked_at"),
        Index("ix_auth_sessions_tenant_provider", "tenant_id", "provider"),
        Index("ix_auth_sessions_active_tenant_id", "active_tenant_id"),
    )
    session_id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    active_tenant_id: Mapped[str | None] = mapped_column(String(255), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class OAuthTransactionModel(Base):
    __tablename__ = "oauth_transactions"
    __table_args__ = (Index("ix_oauth_transactions_expiry", "expires_at", "consumed_at"),)
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    session_binding_hash: Mapped[str | None] = mapped_column(String(64))
    redirect_intent: Mapped[str] = mapped_column(String(1024), nullable=False, default="/")
    code_verifier_ciphertext: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

class AuthAuditEventModel(Base):
    __tablename__ = "auth_audit_events"
    __table_args__ = (Index("ix_auth_audit_tenant_time", "tenant_id", "occurred_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    actor_id: Mapped[str | None] = mapped_column(String(512))
    provider: Mapped[str | None] = mapped_column(String(32))
    connection_id: Mapped[str | None] = mapped_column(String(36))
    session_id_hash: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
