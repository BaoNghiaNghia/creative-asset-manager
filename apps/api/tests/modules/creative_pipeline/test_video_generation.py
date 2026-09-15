import asyncio
import pytest
from app.modules.creative_pipeline.video_generation import GoogleOmniVideoGenerationProvider, SeedanceVideoGenerationProvider, VideoGenerationProviderError, VideoGenerationProviderRegistry, VideoGenerationStatus, VideoGenerationSubmission, transition_generation_run

class Transport:
    async def submit(self, request):
        assert request.idempotency_key.startswith("creative_pipeline:generation:")
        return VideoGenerationSubmission("remote-1", "submitted")
    async def poll(self, request):
        return VideoGenerationStatus("completed")
    async def cancel(self, request):
        return VideoGenerationStatus("cancelled")
    async def fetch_result(self, request):
        raise AssertionError("fetch_result belongs to CP-09")

def test_registry_rejects_duplicates_and_missing():
    registry = VideoGenerationProviderRegistry()
    registry.register(SeedanceVideoGenerationProvider(Transport()))
    registry.register(GoogleOmniVideoGenerationProvider(Transport()))
    assert registry.list_capabilities() == ("google_omni", "seedance")
    with pytest.raises(ValueError):
        registry.register(SeedanceVideoGenerationProvider(Transport()))
    with pytest.raises(VideoGenerationProviderError) as error:
        registry.require("missing")
    assert error.value.code == "creative_video_provider_not_configured"

def test_fake_provider_contract():
    provider = SeedanceVideoGenerationProvider(Transport())
    request = type("Request", (), {"idempotency_key": "creative_pipeline:generation:run-1"})()
    async def run():
        assert (await provider.submit(request)).provider_request_id == "remote-1"
        assert (await provider.poll(object())).state == "completed"
        assert (await provider.cancel(object())).state == "cancelled"
    asyncio.run(run())

def test_generation_state_guard_rejects_terminal_restart():
    run = type("Run", (), {"status": "completed"})()
    with pytest.raises(VideoGenerationProviderError):
        transition_generation_run(run, "running")
