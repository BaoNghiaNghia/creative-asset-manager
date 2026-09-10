from __future__ import annotations

from app.core.config import Settings

def visual_search_tenant_eligible(settings: Settings | None, tenant_id: str) -> bool:
    if settings is None or not settings.VISUAL_SEARCH_ENABLED or not tenant_id:
        return False
    allowed = {value.strip() for value in settings.VISUAL_SEARCH_CANARY_TENANT_IDS.split(",") if value.strip()}
    return tenant_id in allowed

def visual_search_infrastructure_enabled(settings: Settings | None) -> bool:
    return bool(settings and settings.PROCESSING_JOBS_ENABLED and settings.VISUAL_SEARCH_ENABLED and settings.ELASTICSEARCH_URL)
