from __future__ import annotations
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth_persistence.encryption import TokenCipher, TokenEncryptionError
from app.modules.auth_persistence.identity import ApplicationUserInactiveError
from app.modules.auth_persistence.model import AuthAuditEventModel, AuthSessionModel, OAuthConnectionModel, OAuthTransactionModel, TenantMembershipModel, TenantModel, UserModel

def utcnow():
    return datetime.now(timezone.utc)

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)

@dataclass
class PersistentCloudSession:
    session_id_hash: str
    connection_id: str
    tenant_id: str
    user_id: str | None
    active_tenant_id: str | None
    provider: str
    access_token: str
    refresh_token: str | None
    expires_at: float
    user: dict[str, Any]


@dataclass
class PersistentOAuthConnection:
    connection_id: str
    tenant_id: str
    provider: str
    provider_account_id: str
    access_token: str
    refresh_token: str | None
    expires_at: float

class AuthPersistenceRepository:
    def __init__(self, session: Session, cipher: TokenCipher):
        self.session = session
        self.cipher = cipher

    @staticmethod
    def _aad(connection: OAuthConnectionModel, field: str) -> str:
        return f"cam-oauth:{connection.tenant_id}:{connection.provider}:{connection.provider_account_id}:{field}"

    def remember_state(self, *, provider: str, state: str, code_verifier: str | None, ttl_seconds: int, redirect_intent: str = "/", session_binding: str | None = None):
        state_hash = digest(state)
        encrypted = self.cipher.encrypt(code_verifier, aad=f"cam-state:{provider}:{state_hash}")
        row = OAuthTransactionModel(
            state_hash=state_hash, provider=provider,
            session_binding_hash=digest(session_binding) if session_binding else None,
            redirect_intent=redirect_intent[:1024],
            code_verifier_ciphertext=encrypted.ciphertext if encrypted else None,
            key_version=encrypted.key_version if encrypted else self.cipher.active_version,
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
        )
        self.session.add(row)
        self.session.flush()

    def consume_state(self, *, provider: str, state: str, session_binding: str | None = None) -> tuple[str | None, str]:
        now = utcnow()
        state_hash = digest(state)
        binding_hash = digest(session_binding) if session_binding else None
        statement = (
            update(OAuthTransactionModel)
            .where(
                OAuthTransactionModel.state_hash == state_hash,
                OAuthTransactionModel.provider == provider,
                OAuthTransactionModel.consumed_at.is_(None),
                OAuthTransactionModel.expires_at > now,
                or_(OAuthTransactionModel.session_binding_hash.is_(None), OAuthTransactionModel.session_binding_hash == binding_hash),
            )
            .values(consumed_at=now)
            .returning(OAuthTransactionModel)
            .execution_options(synchronize_session=False)
        )
        row = self.session.scalars(statement).first()
        if row is None:
            raise LookupError("invalid_or_expired_oauth_state")
        verifier = self.cipher.decrypt(row.code_verifier_ciphertext, key_version=row.key_version, aad=f"cam-state:{provider}:{state_hash}")
        return verifier, row.redirect_intent

    def upsert_connection(self, *, tenant_id: str, provider: str, provider_account_id: str, account_email: str | None, access_token: str, refresh_token: str | None, expires_at: datetime, scopes: list[str], token_type: str | None, provider_metadata: dict[str, Any] | None = None) -> OAuthConnectionModel:
        row = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.tenant_id == tenant_id,
            OAuthConnectionModel.provider == provider,
            OAuthConnectionModel.provider_account_id == provider_account_id,
        ))
        created = row is None
        if row is None:
            try:
                with self.session.begin_nested():
                    row = OAuthConnectionModel(tenant_id=tenant_id, provider=provider, provider_account_id=provider_account_id, key_version=self.cipher.active_version)
                    self.session.add(row)
                    self.session.flush()
            except IntegrityError:
                row = self.session.scalar(select(OAuthConnectionModel).where(
                    OAuthConnectionModel.tenant_id == tenant_id,
                    OAuthConnectionModel.provider == provider,
                    OAuthConnectionModel.provider_account_id == provider_account_id,
                ))
                if row is None:
                    raise
                created = False
        old_refresh = None
        if refresh_token is None and row.refresh_token_ciphertext:
            old_refresh = self.cipher.decrypt(row.refresh_token_ciphertext, key_version=row.key_version, aad=self._aad(row, "refresh"))
        encrypted_access = self.cipher.encrypt(access_token, aad=self._aad(row, "access"))
        encrypted_refresh = self.cipher.encrypt(refresh_token or old_refresh, aad=self._aad(row, "refresh"))
        row.access_token_ciphertext = encrypted_access.ciphertext
        row.refresh_token_ciphertext = encrypted_refresh.ciphertext if encrypted_refresh else None
        row.key_version = self.cipher.active_version
        row.account_email = account_email
        row.access_token_expires_at = expires_at
        row.scopes_json = sorted(set(scopes))
        row.token_type = token_type
        row.provider_metadata_json = dict(provider_metadata or {})
        row.status = "active"; row.refresh_error_json = None; row.revoked_at = None; row.updated_at = utcnow()
        self.session.flush()
        self.audit("connection_created" if created else "connection_reconnected", tenant_id=tenant_id, provider=provider, connection_id=row.id, actor_id=provider_account_id)
        return row

    def create_session(self, *, connection: OAuthConnectionModel, user: dict[str, Any], ttl_seconds: int, user_id: str | None = None, active_tenant_id: str | None = None) -> tuple[str, AuthSessionModel]:
        encoded_size = len(json.dumps(user, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if encoded_size > 16_384:
            raise ValueError("session user data exceeds limit")
        if user_id is not None:
            application_user = self.session.get(UserModel, user_id)
            if application_user is None:
                raise LookupError("application user not found")
            if application_user.status != "active":
                raise ApplicationUserInactiveError("application user is not active")
        if active_tenant_id is not None:
            if user_id is None:
                raise PermissionError("active tenant requires an application user")
            membership = self.session.scalar(select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == active_tenant_id,
                TenantMembershipModel.user_id == user_id,
                TenantMembershipModel.status == "active",
            ))
            tenant = self.session.get(TenantModel, active_tenant_id)
            if membership is None or tenant is None or tenant.status != "active":
                raise PermissionError("active tenant membership is required")

        session_id = secrets.token_urlsafe(48)
        row = AuthSessionModel(
            session_id_hash=digest(session_id), user_id=user_id, active_tenant_id=active_tenant_id,
            tenant_id=connection.tenant_id, provider=connection.provider,
            connection_id=connection.id, user_json=dict(user),
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
        )
        self.session.add(row); self.session.flush()
        return session_id, row

    def load_session(self, *, provider: str, session_id: str, touch: bool = True, allow_legacy_actor_session: bool = True) -> PersistentCloudSession | None:
        now = utcnow()
        row = self.session.scalar(select(AuthSessionModel).where(
            AuthSessionModel.session_id_hash == digest(session_id),
            AuthSessionModel.provider == provider,
            AuthSessionModel.revoked_at.is_(None),
            AuthSessionModel.expires_at > now,
        ))
        if row is None:
            return None
        if row.user_id is not None:
            application_user = self.session.get(UserModel, row.user_id)
            if application_user is None or application_user.status != "active":
                row.revoked_at = now
                self.audit(
                    "session_revoked", tenant_id=row.tenant_id, provider=provider,
                    connection_id=row.connection_id, session_id_hash=row.session_id_hash,
                    actor_id=row.user_id, detail={"code": "application_user_inactive"},
                )
                self.session.flush()
                return None
        elif not allow_legacy_actor_session:
            row.revoked_at = now
            self.audit(
                "session_revoked", tenant_id=row.tenant_id, provider=provider,
                connection_id=row.connection_id, session_id_hash=row.session_id_hash,
                detail={"code": "legacy_actor_session_expired"},
            )
            self.session.flush(); return None
        connection = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.id == row.connection_id,
            OAuthConnectionModel.tenant_id == row.tenant_id,
            OAuthConnectionModel.status.in_(("active", "refresh_error")),
        ))
        if connection is None:
            return None
        try:
            access = self.cipher.decrypt(connection.access_token_ciphertext, key_version=connection.key_version, aad=self._aad(connection, "access"))
            refresh = self.cipher.decrypt(connection.refresh_token_ciphertext, key_version=connection.key_version, aad=self._aad(connection, "refresh"))
        except TokenEncryptionError:
            connection.status = "reconnect_required"
            connection.refresh_error_json = {"code": "token_decryption_failed", "retryable": False}
            self.audit("reconnect_required", tenant_id=row.tenant_id, provider=provider, connection_id=connection.id, actor_id=connection.provider_account_id, detail={"code": "token_decryption_failed"})
            self.session.flush()
            return None
        if not access:
            return None
        if touch:
            row.last_seen_at = now
            self.session.flush()
        return PersistentCloudSession(
            row.session_id_hash, connection.id, row.tenant_id, row.user_id,
            row.active_tenant_id, provider, access, refresh,
            aware(connection.access_token_expires_at).timestamp()
            if connection.access_token_expires_at else 0,
            dict(row.user_json),
        )

    def load_connection(
        self, *, provider: str, connection_id: str
    ) -> PersistentOAuthConnection | None:
        connection = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.id == connection_id,
            OAuthConnectionModel.provider == provider,
            OAuthConnectionModel.status.in_(("active", "refresh_error")),
        ))
        if connection is None:
            return None
        try:
            access = self.cipher.decrypt(
                connection.access_token_ciphertext,
                key_version=connection.key_version,
                aad=self._aad(connection, "access"),
            )
            refresh = self.cipher.decrypt(
                connection.refresh_token_ciphertext,
                key_version=connection.key_version,
                aad=self._aad(connection, "refresh"),
            )
        except TokenEncryptionError:
            return None
        if not access:
            return None
        return PersistentOAuthConnection(
            connection.id, connection.tenant_id, connection.provider,
            connection.provider_account_id, access, refresh,
            aware(connection.access_token_expires_at).timestamp()
            if connection.access_token_expires_at else 0,
        )


    def revoke_session(self, *, provider: str, session_id: str) -> bool:
        now = utcnow()
        row = self.session.get(AuthSessionModel, digest(session_id))
        if row is None or row.provider != provider or row.revoked_at is not None:
            return False
        row.revoked_at = now
        self.audit("session_revoked", tenant_id=row.tenant_id, provider=provider, connection_id=row.connection_id, session_id_hash=row.session_id_hash)
        self.session.flush()
        return True

    def rotate_session_active_tenant(
        self, *, provider: str, session_id: str, user_id: str,
        tenant_id: str, ttl_seconds: int,
    ) -> tuple[str, AuthSessionModel]:
        now = utcnow()
        current = self.session.scalar(
            select(AuthSessionModel).where(
                AuthSessionModel.session_id_hash == digest(session_id),
                AuthSessionModel.provider == provider,
                AuthSessionModel.user_id == user_id,
                AuthSessionModel.revoked_at.is_(None),
                AuthSessionModel.expires_at > now,
            ).with_for_update()
        )
        if current is None:
            raise LookupError("application session is not active")
        user = self.session.get(UserModel, user_id)
        membership = self.session.scalar(select(TenantMembershipModel).where(
            TenantMembershipModel.tenant_id == tenant_id,
            TenantMembershipModel.user_id == user_id,
            TenantMembershipModel.status == "active",
        ))
        tenant = self.session.get(TenantModel, tenant_id)
        if user is None or user.status != "active":
            raise ApplicationUserInactiveError("application user is not active")
        if membership is None or tenant is None or tenant.status != "active":
            raise PermissionError("active tenant membership is required")
        connection = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.id == current.connection_id,
            OAuthConnectionModel.tenant_id == current.tenant_id,
            OAuthConnectionModel.provider == provider,
        ))
        if connection is None:
            raise LookupError("OAuth connection is unavailable")
        replacement_id, replacement = self.create_session(
            connection=connection,
            user=dict(current.user_json),
            ttl_seconds=ttl_seconds,
            user_id=user_id,
            active_tenant_id=tenant_id,
        )
        previous_tenant_id = current.active_tenant_id
        current.revoked_at = now
        self.audit(
            "active_tenant_selected", tenant_id=tenant_id, actor_id=user_id,
            provider=provider, connection_id=connection.id,
            session_id_hash=replacement.session_id_hash,
            detail={
                "previous_tenant_id": previous_tenant_id,
                "membership_id": membership.id,
            },
        )
        self.session.flush()
        return replacement_id, replacement

    def claim_refresh(self, *, tenant_id: str, connection_id: str, owner: str, lease_seconds: int) -> bool:
        now = utcnow()
        result = self.session.execute(
            update(OAuthConnectionModel)
            .where(
                OAuthConnectionModel.id == connection_id,
                OAuthConnectionModel.tenant_id == tenant_id,
                OAuthConnectionModel.status.in_(("active", "refresh_error")),
                or_(OAuthConnectionModel.refresh_claimed_by.is_(None), OAuthConnectionModel.refresh_lease_expires_at <= now),
            )
            .values(refresh_claimed_by=owner, refresh_lease_expires_at=now + timedelta(seconds=lease_seconds), updated_at=now)
            .execution_options(synchronize_session=False)
        )
        return bool(result.rowcount)

    def finish_refresh(self, *, tenant_id: str, connection_id: str, owner: str, access_token: str, refresh_token: str | None, expires_at: datetime, scopes: list[str] | None = None, token_type: str | None = None):
        row = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.id == connection_id, OAuthConnectionModel.tenant_id == tenant_id,
            OAuthConnectionModel.refresh_claimed_by == owner,
        ).with_for_update())
        if row is None:
            raise LookupError("refresh_lock_lost")
        old_refresh = self.cipher.decrypt(row.refresh_token_ciphertext, key_version=row.key_version, aad=self._aad(row, "refresh"))
        access = self.cipher.encrypt(access_token, aad=self._aad(row, "access"))
        refresh = self.cipher.encrypt(refresh_token or old_refresh, aad=self._aad(row, "refresh"))
        row.access_token_ciphertext = access.ciphertext
        row.refresh_token_ciphertext = refresh.ciphertext if refresh else None
        row.key_version = self.cipher.active_version
        row.access_token_expires_at = expires_at
        if scopes is not None: row.scopes_json = sorted(set(scopes))
        if token_type is not None: row.token_type = token_type
        row.status = "active"; row.last_refresh_at = utcnow(); row.refresh_error_json = None
        row.refresh_claimed_by = None; row.refresh_lease_expires_at = None; row.updated_at = utcnow()
        self.audit("connection_refreshed", tenant_id=tenant_id, provider=row.provider, connection_id=row.id, actor_id=row.provider_account_id)
        self.session.flush()

    def fail_refresh(self, *, tenant_id: str, connection_id: str, owner: str, code: str, retryable: bool):
        row = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.id == connection_id, OAuthConnectionModel.tenant_id == tenant_id,
            OAuthConnectionModel.refresh_claimed_by == owner,
        ).with_for_update())
        if row is None:
            return
        row.status = "refresh_error" if retryable else "reconnect_required"
        row.refresh_error_json = {"code": code[:100], "retryable": retryable, "occurred_at": utcnow().isoformat()}
        row.refresh_claimed_by = None; row.refresh_lease_expires_at = None; row.updated_at = utcnow()
        self.audit("refresh_failed" if retryable else "reconnect_required", tenant_id=tenant_id, provider=row.provider, connection_id=row.id, actor_id=row.provider_account_id, detail={"code": code[:100], "retryable": retryable})
        self.session.flush()

    def revoke_connection(self, *, tenant_id: str, provider: str, provider_account_id: str, actor_id: str, reason: str | None = None) -> bool:
        row = self.session.scalar(select(OAuthConnectionModel).where(
            OAuthConnectionModel.tenant_id == tenant_id,
            OAuthConnectionModel.provider == provider,
            OAuthConnectionModel.provider_account_id == provider_account_id,
        ).with_for_update())
        if row is None or row.status == "revoked":
            return False
        now = utcnow()
        row.status = "revoked"; row.revoked_at = now; row.updated_at = now
        row.refresh_claimed_by = None; row.refresh_lease_expires_at = None
        row.access_token_ciphertext = None; row.refresh_token_ciphertext = None
        self.session.execute(update(AuthSessionModel).where(
            AuthSessionModel.tenant_id == tenant_id,
            AuthSessionModel.connection_id == row.id,
            AuthSessionModel.revoked_at.is_(None),
        ).values(revoked_at=now))
        self.audit("connection_revoked", tenant_id=tenant_id, provider=provider, connection_id=row.id, actor_id=actor_id, detail={"reason": (reason or "")[:500]})
        self.session.flush()
        return True

    def rotate_page(self, *, after_id: str | None, limit: int, dry_run: bool) -> tuple[int, str | None]:
        statement = select(OAuthConnectionModel).where(OAuthConnectionModel.key_version != self.cipher.active_version)
        if after_id: statement = statement.where(OAuthConnectionModel.id > after_id)
        rows = list(self.session.scalars(statement.order_by(OAuthConnectionModel.id).limit(limit)))
        for row in rows:
            access = self.cipher.decrypt(row.access_token_ciphertext, key_version=row.key_version, aad=self._aad(row, "access"))
            refresh = self.cipher.decrypt(row.refresh_token_ciphertext, key_version=row.key_version, aad=self._aad(row, "refresh"))
            if not dry_run:
                enc_access = self.cipher.encrypt(access, aad=self._aad(row, "access"))
                enc_refresh = self.cipher.encrypt(refresh, aad=self._aad(row, "refresh"))
                old_version = row.key_version
                row.access_token_ciphertext = enc_access.ciphertext if enc_access else None
                row.refresh_token_ciphertext = enc_refresh.ciphertext if enc_refresh else None
                row.key_version = self.cipher.active_version; row.updated_at = utcnow()
                self.audit("key_rotated", tenant_id=row.tenant_id, provider=row.provider, connection_id=row.id, actor_id="operator", detail={"from_version": old_version, "to_version": self.cipher.active_version})
        self.session.flush()
        return len(rows), rows[-1].id if rows else None

    def cleanup_expired(self) -> dict[str, int]:
        now = utcnow()
        sessions = self.session.execute(delete(AuthSessionModel).where(AuthSessionModel.expires_at <= now)).rowcount or 0
        states = self.session.execute(delete(OAuthTransactionModel).where(OAuthTransactionModel.expires_at <= now)).rowcount or 0
        return {"sessions": sessions, "oauth_states": states}

    def audit(self, action: str, *, tenant_id=None, actor_id=None, provider=None, connection_id=None, session_id_hash=None, detail=None):
        self.session.add(AuthAuditEventModel(tenant_id=tenant_id, actor_id=actor_id, provider=provider, connection_id=connection_id, session_id_hash=session_id_hash, action=action, detail_json=dict(detail or {})))
