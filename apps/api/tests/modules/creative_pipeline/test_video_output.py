import asyncio
import hashlib
import pytest
from types import SimpleNamespace
from app.modules.creative_pipeline.video_output import raw_artifact_version
from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.video_generation import VideoGenerationResult
from app.modules.creative_pipeline.storage import StorageItem, PipelineStorageError

def test_raw_version_mapping():
    assert raw_artifact_version(1, "seedance") == 1
    assert raw_artifact_version(1, "google_omni") == 2
    assert raw_artifact_version(2, "seedance") == 3
    assert raw_artifact_version(2, "google_omni") == 4

class Gateway:
    def __init__(self):
        self.items = {}
        self.uploads = []
    async def get_item(self, item_id):
        return self.items.get(item_id)
    async def list_children(self, parent_id):
        return [item for item in self.items.values() if item.parent_id == parent_id]
    async def upload_bytes(self, parent_id, name, mime, content):
        item = StorageItem("uploaded", name, parent_id, "other")
        self.items[item.id] = item
        self.uploads.append((name, mime, content))
        return item
    async def rename_item(self, item_id, name):
        old = self.items[item_id]
        item = StorageItem(item_id, name, old.parent_id, old.kind)
        self.items[item_id] = item
        return item

def test_materialize_video_streams_hashes_and_bounds():
    payload = b"....ftypisom" + b"x" * 32
    gateway = Gateway()
    artifact = SimpleNamespace(status="reserved", external_file_id=None, relative_path="Pipeline/Video Output/1x1/v001.mp4", id="a1", content_hash=None, size_bytes=None, mime_type=None, available_at=None, _artifact_parent_id="ratio")
    result = VideoGenerationResult("request-1", "video/mp4", content=payload, checksum=hashlib.sha256(payload).hexdigest())
    async def run():
        await ArtifactService(None).materialize_video(artifact, gateway, result, max_output_bytes=1024)
    asyncio.run(run())
    assert artifact.status == "available"
    assert artifact.size_bytes == len(payload)
    assert artifact.content_hash == hashlib.sha256(payload).hexdigest()
    assert gateway.uploads[0][0].startswith(".__cp_tmp_a1")
    assert gateway.uploads[0][1] == "video/mp4"

def test_materialize_video_rejects_invalid_mime_and_size():
    gateway = Gateway()
    artifact = SimpleNamespace(status="reserved", external_file_id=None, relative_path="v001.mp4", id="a2", _artifact_parent_id="ratio")
    async def run():
        with pytest.raises(PipelineStorageError):
            await ArtifactService(None).materialize_video(artifact, gateway, VideoGenerationResult("r", "video/webm", content=b"ftyp"), max_output_bytes=10)
        with pytest.raises(PipelineStorageError):
            await ArtifactService(None).materialize_video(artifact, gateway, VideoGenerationResult("r", "video/mp4", content=b"ftyp-too-large"), max_output_bytes=3)
    asyncio.run(run())
