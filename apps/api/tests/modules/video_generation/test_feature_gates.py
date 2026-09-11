from app.core.config import Settings
from app.modules.video_generation.service import enabled

def test_defaults_are_deny_by_default():
    settings = Settings()
    assert settings.VIDEO_GENERATION_ENABLED is False
    assert settings.DOLA_RENDER_GATEWAY_ENABLED is False
    assert enabled(settings, "tenant-a") is False

def test_canary_requires_all_gates():
    settings = Settings(PROCESSING_JOBS_ENABLED=True, MANAGED_ASSET_STORAGE_ENABLED=True, VIDEO_GENERATION_ENABLED=True, DOLA_RENDER_GATEWAY_ENABLED=True, VIDEO_GENERATION_CANARY_TENANT_IDS="tenant-a")
    assert enabled(settings, "tenant-a") is True
    assert enabled(settings, "tenant-b") is False
