from __future__ import annotations
import hashlib
from dataclasses import dataclass
from threading import Lock
from typing import Callable
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.modules.auth_persistence.encryption import TokenCipher, TokenEncryptionError
from app.modules.inventory.persistence_model import (
    InventoryAiCredentialAuditModel,
    InventoryAiCredentialModel,
)
class InventoryCredentialError(RuntimeError): pass

def inventory_backup_provider(slot: int) -> str:
    if slot < 1:
        raise ValueError("inventory_gemini_backup_slot_invalid")
    return f"gemini_backup_{slot}"


def inventory_backup_slot(provider: str) -> int | None:
    prefix = "gemini_backup_"
    if not provider.startswith(prefix):
        return None
    suffix = provider[len(prefix):]
    if not suffix.isdigit():
        return None
    slot = int(suffix)
    return slot if slot >= 1 else None


def is_inventory_gemini_provider(provider: str) -> bool:
    return provider == "gemini" or inventory_backup_slot(provider) is not None

def inventory_credential_cipher(settings: Settings) -> TokenCipher:
    value=settings.INVENTORY_CREDENTIAL_ENCRYPTION_KEY.strip()
    if not value: raise InventoryCredentialError("inventory_credential_encryption_unavailable")
    try: return TokenCipher.from_config(f"v1:{value}", "v1")
    except ValueError as exc: raise InventoryCredentialError("inventory_credential_encryption_unavailable") from exc
@dataclass(frozen=True, slots=True)
class InventoryCredentialMetadata:
    id:str; tenant_id:str; provider:str; secret_fingerprint:str; secret_last4:str; label:str|None; status:str; last_tested_at:datetime|None; last_test_status:str|None; created_at:datetime; updated_at:datetime; updated_by:str|None
class InventoryAiCredentialRepository:
    def __init__(self, session:Session, cipher:TokenCipher | None): self.session,self.cipher=session,cipher
    @staticmethod
    def _aad(tenant_id:str, provider:str)->str: return f"inventory-ai-credential:{tenant_id}:{provider}"
    @staticmethod
    def _metadata(row:InventoryAiCredentialModel)->InventoryCredentialMetadata:
        return InventoryCredentialMetadata(row.id,row.tenant_id,row.provider,row.secret_fingerprint,row.secret_last4,row.label,row.status,row.last_tested_at,row.last_test_status,row.created_at,row.updated_at,row.updated_by)
    def get_metadata(self,tenant_id:str,provider:str="gemini"):
        row=self.session.scalar(select(InventoryAiCredentialModel).where(InventoryAiCredentialModel.tenant_id==tenant_id,InventoryAiCredentialModel.provider==provider)); return self._metadata(row) if row else None
    def list_backup_metadata(self,tenant_id:str):
        rows=self.session.scalars(select(InventoryAiCredentialModel).where(
            InventoryAiCredentialModel.tenant_id==tenant_id,
            InventoryAiCredentialModel.provider.like("gemini_backup_%"),
        )).all()
        valid=[row for row in rows if inventory_backup_slot(row.provider) is not None]
        valid.sort(key=lambda row: inventory_backup_slot(row.provider) or 0)
        return [self._metadata(row) for row in valid]
    def list_active_providers(self,tenant_id:str)->tuple[str,...]:
        providers=[]
        primary=self.get_metadata(tenant_id)
        if primary is not None and primary.status=="active": providers.append("gemini")
        providers.extend(
            item.provider for item in self.list_backup_metadata(tenant_id)
            if item.status=="active"
        )
        return tuple(providers)
    def get_active_secret(self,tenant_id:str,provider:str="gemini"):
        row=self.session.scalar(select(InventoryAiCredentialModel).where(InventoryAiCredentialModel.tenant_id==tenant_id,InventoryAiCredentialModel.provider==provider,InventoryAiCredentialModel.status=="active"))
        if not row:return None
        if self.cipher is None: raise InventoryCredentialError("inventory_credential_encryption_unavailable")
        try: secret=self.cipher.decrypt(row.encrypted_secret,key_version=row.key_version,aad=self._aad(tenant_id,provider))
        except (TokenEncryptionError,ValueError) as exc: raise InventoryCredentialError("inventory_ai_credential_decryption_failed") from exc
        if not secret: raise InventoryCredentialError("inventory_ai_credential_decryption_failed")
        return secret
    def replace(self,tenant_id:str,*,provider:str="gemini",secret:str,label:str|None=None,updated_by:str|None=None,last_test_status:str|None=None):
        if not is_inventory_gemini_provider(provider):raise ValueError("inventory_ai_provider_unsupported")
        if not secret or secret.strip()!=secret or len(secret)>512:raise ValueError("inventory_ai_credential_invalid")
        if label is not None and len(label.strip())>255:raise ValueError("inventory_ai_credential_label_invalid")
        if self.cipher is None: raise InventoryCredentialError("inventory_credential_encryption_unavailable")
        encrypted=self.cipher.encrypt(secret,aad=self._aad(tenant_id,provider))
        if encrypted is None:raise InventoryCredentialError("inventory_credential_encryption_unavailable")
        row=self.session.scalar(select(InventoryAiCredentialModel).where(InventoryAiCredentialModel.tenant_id==tenant_id,InventoryAiCredentialModel.provider==provider))
        if row is None: row=InventoryAiCredentialModel(tenant_id=tenant_id,provider=provider);self.session.add(row)
        row.encrypted_secret=encrypted.ciphertext;row.key_version=encrypted.key_version;row.secret_fingerprint=hashlib.sha256(secret.encode()).hexdigest();row.secret_last4=secret[-4:];row.label=label.strip() if label else None;row.status="active";row.last_tested_at=datetime.now(timezone.utc) if last_test_status else None;row.last_test_status=last_test_status or "not_tested";row.updated_by=updated_by;self.session.flush();return self._metadata(row)
    def record_test_result(self,tenant_id:str,*,result:str,provider:str="gemini",tested_at:datetime|None=None):
        row=self.session.scalar(select(InventoryAiCredentialModel).where(InventoryAiCredentialModel.tenant_id==tenant_id,InventoryAiCredentialModel.provider==provider))
        if row is None:return None
        row.last_tested_at=tested_at or datetime.now(timezone.utc);row.last_test_status=result;self.session.flush();return self._metadata(row)
    def delete(self,tenant_id:str,provider:str="gemini"):
        row=self.session.scalar(select(InventoryAiCredentialModel).where(InventoryAiCredentialModel.tenant_id==tenant_id,InventoryAiCredentialModel.provider==provider))
        if not row:return False
        self.session.delete(row);self.session.flush();return True

    def audit(
        self, tenant_id: str, *, actor_id: str | None, action: str, result: str,
        previous_fingerprint: str | None = None, new_fingerprint: str | None = None,
        provider: str = "gemini",
    ) -> None:
        """Append safe metadata without ever persisting a secret."""
        self.session.add(InventoryAiCredentialAuditModel(
            tenant_id=tenant_id, provider=provider, actor_id=actor_id, action=action,
            result=result, previous_fingerprint=previous_fingerprint,
            new_fingerprint=new_fingerprint,
        ))
        self.session.flush()


class InventoryGeminiCredentialResolver:
    """Resolves and rotates tenant Inventory Gemini credentials."""

    def __init__(self, session_factory: Callable[[], Session], settings: Settings):
        self.session_factory = session_factory
        self.settings = settings
        self._rotation_lock = Lock()
        self._next_index_by_tenant: dict[str, int] = {}

    def resolve(self, tenant_id: str) -> str:
        # Any configured Inventory credential takes precedence over the
        # deployment fallback. Broken configured credentials fail closed.
        with self.session_factory() as session:
            providers = InventoryAiCredentialRepository(
                session, None
            ).list_active_providers(tenant_id)
        if providers:
            with self._rotation_lock:
                index = self._next_index_by_tenant.get(tenant_id, 0) % len(providers)
                self._next_index_by_tenant[tenant_id] = index + 1
            provider = providers[index]
            cipher = inventory_credential_cipher(self.settings)
            with self.session_factory() as session:
                secret = InventoryAiCredentialRepository(
                    session, cipher
                ).get_active_secret(tenant_id, provider=provider)
            if secret is None:
                raise InventoryCredentialError("inventory_ai_credential_decryption_failed")
            return secret
        fallback = self.settings.inventory_gemini_api_key
        if fallback:
            return fallback
        raise InventoryCredentialError("inventory_gemini_credential_unavailable")
