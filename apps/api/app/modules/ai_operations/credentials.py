from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_operations.credential_model import CreativeAiCredentialAuditModel, CreativeAiCredentialModel
from app.modules.auth_persistence.encryption import TokenCipher, TokenEncryptionError


class CreativeCredentialError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def creative_credential_cipher(settings: Settings) -> TokenCipher:
    value = settings.CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY.strip()
    if not value:
        raise CreativeCredentialError("creative_credential_encryption_unavailable")
    try:
        return TokenCipher.from_config(f"v1:{value}", "v1")
    except ValueError as exc:
        raise CreativeCredentialError("creative_credential_encryption_unavailable") from exc


@dataclass(frozen=True, slots=True)
class CreativeGeminiCredential:
    secret: str
    fingerprint: str
    source: str
    last4: str
    encrypted_secret: str | None = None
    key_version: str | None = None


@dataclass(frozen=True, slots=True)
class CreativeCredentialMetadata:
    id: str; tenant_id: str; provider: str; secret_fingerprint: str; secret_last4: str
    label: str | None; status: str; last_tested_at: datetime | None; last_test_status: str | None
    created_at: datetime; updated_at: datetime; updated_by: str | None


class CreativeAiCredentialRepository:
    def __init__(self, session: Session, cipher: TokenCipher | None):
        self.session, self.cipher = session, cipher

    @staticmethod
    def _aad(tenant_id: str, provider: str) -> str:
        return f"creative-ai-credential:{tenant_id}:{provider}"

    @staticmethod
    def _metadata(row: CreativeAiCredentialModel) -> CreativeCredentialMetadata:
        return CreativeCredentialMetadata(row.id, row.tenant_id, row.provider, row.secret_fingerprint, row.secret_last4, row.label, row.status, row.last_tested_at, row.last_test_status, row.created_at, row.updated_at, row.updated_by)

    def get_metadata(self, tenant_id: str, provider: str = "gemini") -> CreativeCredentialMetadata | None:
        row = self.session.scalar(select(CreativeAiCredentialModel).where(CreativeAiCredentialModel.tenant_id == tenant_id, CreativeAiCredentialModel.provider == provider))
        return self._metadata(row) if row else None

    def get_active_secret(self, tenant_id: str, provider: str = "gemini") -> CreativeGeminiCredential | None:
        row = self.session.scalar(select(CreativeAiCredentialModel).where(CreativeAiCredentialModel.tenant_id == tenant_id, CreativeAiCredentialModel.provider == provider, CreativeAiCredentialModel.status == "active"))
        if row is None:
            return None
        if self.cipher is None:
            raise CreativeCredentialError("creative_credential_encryption_unavailable")
        try:
            secret = self.cipher.decrypt(row.encrypted_secret, key_version=row.key_version, aad=self._aad(tenant_id, provider))
        except (TokenEncryptionError, ValueError) as exc:
            raise CreativeCredentialError("creative_ai_credential_decryption_failed") from exc
        if not secret:
            raise CreativeCredentialError("creative_ai_credential_decryption_failed")
        return CreativeGeminiCredential(secret, row.secret_fingerprint, "configuration", row.secret_last4, row.encrypted_secret, row.key_version)

    def replace(self, tenant_id: str, *, secret: str, label: str | None = None, updated_by: str | None = None, last_test_status: str = "VALID") -> CreativeCredentialMetadata:
        if not secret or secret.strip() != secret or len(secret) > 512:
            raise ValueError("creative_ai_credential_invalid")
        if label is not None and len(label.strip()) > 255:
            raise ValueError("creative_ai_credential_label_invalid")
        if self.cipher is None:
            raise CreativeCredentialError("creative_credential_encryption_unavailable")
        encrypted = self.cipher.encrypt(secret, aad=self._aad(tenant_id, "gemini"))
        assert encrypted is not None
        row = self.session.scalar(select(CreativeAiCredentialModel).where(CreativeAiCredentialModel.tenant_id == tenant_id, CreativeAiCredentialModel.provider == "gemini"))
        if row is None:
            row = CreativeAiCredentialModel(tenant_id=tenant_id, provider="gemini")
            self.session.add(row)
        row.encrypted_secret, row.key_version = encrypted.ciphertext, encrypted.key_version
        row.secret_fingerprint, row.secret_last4 = hashlib.sha256(secret.encode()).hexdigest(), secret[-4:]
        row.label, row.status, row.last_tested_at, row.last_test_status, row.updated_by = (label.strip() if label else None), "active", datetime.now(timezone.utc), last_test_status, updated_by
        self.session.flush()
        return self._metadata(row)

    def audit(self, tenant_id: str, *, actor_id: str | None, action: str, result: str, previous_fingerprint: str | None = None, new_fingerprint: str | None = None) -> None:
        self.session.add(CreativeAiCredentialAuditModel(tenant_id=tenant_id, provider="gemini", actor_id=actor_id, action=action, result=result, previous_fingerprint=previous_fingerprint, new_fingerprint=new_fingerprint))
        self.session.flush()


class CreativeGeminiCredentialResolver:
    """Resolves a tenant key for every Creative Gemini request; no static worker key."""
    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory, self.settings = session_factory, settings

    def resolve(self, tenant_id: str) -> CreativeGeminiCredential:
        with self.session_factory() as session:
            override = CreativeAiCredentialRepository(session, None).get_metadata(tenant_id)
        if override is not None and override.status == "active":
            cipher = creative_credential_cipher(self.settings)
            with self.session_factory() as session:
                resolved = CreativeAiCredentialRepository(session, cipher).get_active_secret(tenant_id)
            if resolved is None:
                raise CreativeCredentialError("creative_ai_credential_decryption_failed")
            return resolved
        fallback = (self.settings.GEMINI_API_KEY or "").strip()
        if fallback:
            return CreativeGeminiCredential(fallback, hashlib.sha256(fallback.encode()).hexdigest(), "environment", fallback[-4:])
        raise CreativeCredentialError("creative_gemini_credential_unavailable")
