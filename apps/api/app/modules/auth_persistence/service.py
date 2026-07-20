from __future__ import annotations
from collections import Counter
from contextlib import contextmanager
from threading import Lock

from fastapi import HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.auth_persistence.encryption import TokenCipher
from app.modules.auth_persistence.repository import AuthPersistenceRepository

class AuthMetrics:
    _allowed = {"connection_created", "connection_refreshed", "connection_revoked", "reconnect_required", "key_rotated", "session_revoked", "refresh_lock_contention"}
    def __init__(self):
        self._counter = Counter(); self._lock = Lock()
    def increment(self, event: str, provider: str | None = None):
        key = (event if event in self._allowed else "other", provider if provider in {"google", "microsoft"} else "other")
        with self._lock: self._counter[key] += 1
    def snapshot(self):
        with self._lock: return {f"{event}:{provider}": count for (event, provider), count in self._counter.items()}

AUTH_METRICS = AuthMetrics()

def cipher_from_settings():
    settings = get_settings()
    if not settings.PERSISTENT_AUTH_ENABLED:
        raise HTTPException(503, "Persistent authentication is not enabled")
    try:
        return TokenCipher.from_config(settings.OAUTH_TOKEN_ENCRYPTION_KEYS, settings.OAUTH_ACTIVE_KEY_VERSION)
    except ValueError as exc:
        raise HTTPException(503, "OAuth token encryption is not configured") from exc

@contextmanager
def auth_repository():
    with SessionLocal() as session:
        repository = AuthPersistenceRepository(session, cipher_from_settings())
        try:
            yield repository
            session.commit()
        except Exception:
            session.rollback()
            raise

def cookie_options():
    settings = get_settings()
    return {
        "max_age": settings.AUTH_SESSION_TTL_SECONDS,
        "httponly": True,
        "secure": settings.AUTH_COOKIE_SECURE,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
        "path": settings.AUTH_COOKIE_PATH,
        **({"domain": settings.AUTH_COOKIE_DOMAIN} if settings.AUTH_COOKIE_DOMAIN else {}),
    }

def delete_cookie_options():
    settings = get_settings()
    return {
        "path": settings.AUTH_COOKIE_PATH,
        **({"domain": settings.AUTH_COOKIE_DOMAIN} if settings.AUTH_COOKIE_DOMAIN else {}),
    }
