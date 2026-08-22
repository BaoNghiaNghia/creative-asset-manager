from __future__ import annotations

from typing import TYPE_CHECKING

from app.common.cache import BoundedTTLCache, CacheMetrics

if TYPE_CHECKING:
    from app.modules.authorization.principal import CurrentPrincipal


PrincipalCacheKey = tuple[str, str]


class PrincipalCache:
    """Short-lived cache for immutable, successfully resolved principals."""

    def __init__(self, *, max_entries: int = 2048, ttl_seconds: float = 20):
        self._cache: BoundedTTLCache[PrincipalCacheKey, CurrentPrincipal] = (
            BoundedTTLCache(max_entries=max_entries, ttl_seconds=ttl_seconds)
        )

    def get(
        self, session_hash: str, tenant_id: str | None
    ) -> CurrentPrincipal | None:
        return self._cache.get((session_hash, tenant_id or ""))

    def put(self, principal: CurrentPrincipal) -> None:
        self._cache.put(
            (principal.session_id, principal.active_tenant_id), principal
        )

    def invalidate_session(self, session_hash: str) -> int:
        return self._cache.invalidate_where(lambda key: key[0] == session_hash)

    def invalidate_user(self, user_id: str) -> int:
        return self._cache.invalidate_matching(
            lambda _key, principal: principal.user_id == user_id
        )

    def invalidate_tenant(self, tenant_id: str) -> int:
        return self._cache.invalidate_where(lambda key: key[1] == tenant_id)

    def metrics(self) -> CacheMetrics:
        return self._cache.metrics()

    def clear(self) -> None:
        self._cache.clear()


principal_cache = PrincipalCache()
