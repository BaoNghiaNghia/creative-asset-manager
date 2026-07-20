from __future__ import annotations

from app.core.config import Settings
from app.modules.auth_persistence.encryption import TokenCipher


def sensitive_url_cipher(settings: Settings) -> TokenCipher:
    return TokenCipher.from_config(
        settings.SENSITIVE_URL_ENCRYPTION_KEYS,
        settings.SENSITIVE_URL_ACTIVE_KEY_VERSION,
    )


def url_aad(tenant_id: str, item_id: str) -> str:
    return f"external-ingestion-url:{tenant_id}:{item_id}"
