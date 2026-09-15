from pathlib import Path
import asyncio, hashlib
from types import SimpleNamespace
from app.modules.creative_pipeline.enhancement import EnhancementPolicy, VideoEnhancementInput, VideoEnhancementResult, VideoEnhancementProviderRegistry, VideoEnhancementError
from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.storage import StorageItem

def test_policy_requires_explicit_version_and_keeps_only_configured_settings():
    assert EnhancementPolicy.from_config(None) is None
    policy = EnhancementPolicy.from_config({"version": "v1", "watermark_removal_enabled": True, "smart_enhance_enabled": True, "upscale_factor": 2})
    assert policy.version == "v1"
    assert policy.settings == {"upscale_factor": 2}

def test_provider_registry_is_dependency_injected():
    provider = object()
    registry = VideoEnhancementProviderRegistry({"default": provider})
    assert registry.get() is provider

class StreamingGateway:
    def __init__(self):
        self.items = {}
        self.streamed = bytearray()
    async def get_item(self, item_id): return self.items.get(item_id)
    async def list_children(self, parent_id): return []
    async def upload_file(self, parent_id, name, mime, path):
        with open(path, "rb") as handle:
            while chunk := handle.read(7):
                self.streamed.extend(chunk)
        item = StorageItem("f1", name, parent_id, "other")
        self.items[item.id] = item
        return item
    async def upload_bytes(self, *args): raise AssertionError("byte upload must not be used")
    async def rename_item(self, item_id, name):
        old = self.items[item_id]
        item = StorageItem(item_id, name, old.parent_id, old.kind)
        self.items[item_id] = item
        return item

def test_materialize_video_uses_file_upload_without_reading_staged_file(monkeypatch, tmp_path):
    payload = b"xxxxftypisom" + b"x" * 40
    artifact = SimpleNamespace(status="reserved", external_file_id=None, relative_path="Pipeline/Watermark & Smart Enhance/1x1/v001_enhanced.mp4", id="enh-1", content_hash=None, size_bytes=None, mime_type=None, available_at=None, _artifact_parent_id="ratio")
    gateway = StreamingGateway()
    result = VideoEnhancementResult("video/mp4", payload, checksum=hashlib.sha256(payload).hexdigest())
    asyncio.run(ArtifactService(None).materialize_video(artifact, gateway, result, max_output_bytes=1024))
    assert artifact.status == "available"
    assert bytes(gateway.streamed) == payload
