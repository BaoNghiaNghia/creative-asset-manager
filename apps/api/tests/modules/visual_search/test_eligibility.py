from app.core.config import Settings
from app.modules.visual_search.eligibility import visual_search_tenant_eligible


def test_canary_tenant_eligibility_normalizes_allowlist_entries() -> None:
    settings = Settings(VISUAL_SEARCH_ENABLED=True, VISUAL_SEARCH_CANARY_TENANT_IDS=" , tenant-a, tenant-b ,")
    assert visual_search_tenant_eligible(settings, "tenant-a")
    assert visual_search_tenant_eligible(settings, "tenant-b")
    assert not visual_search_tenant_eligible(settings, "tenant-c")


def test_canary_tenant_eligibility_defaults_to_deny() -> None:
    assert not visual_search_tenant_eligible(Settings(VISUAL_SEARCH_ENABLED=True), "tenant-a")
    assert not visual_search_tenant_eligible(Settings(VISUAL_SEARCH_CANARY_TENANT_IDS="tenant-a"), "tenant-a")
