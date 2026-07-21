from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.search.shadow import SearchShadowComparator

SHADOW_SEARCH = SearchShadowComparator(
    session_factory=SessionLocal,
    global_enabled=lambda: bool(
        get_settings().SEARCH_SHADOW_COMPARISON_ENABLED
        and get_settings().ELASTICSEARCH_URL
    ),
    max_timeout_ms=get_settings().SEARCH_SHADOW_MAX_TIMEOUT_MS,
)
