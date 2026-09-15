import asyncio
import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.constants import ArtifactType
from app.modules.creative_pipeline.model import ArtifactModel, ListingTaskModel, PipelineRunModel
from app.modules.creative_pipeline.storage import PipelineStorageAmbiguous, PipelineStorageError, StorageItem, ensure_pipeline_structure
from tests.modules.creative_pipeline.test_domain import _group, _listing, _session


class FakeStorage:
    supports_writes = True
    def __init__(self, items=()):
        self.items = {item.id: item for item in items}
        self.counter = 0
        self.uploads = []
    async def get_item(self, item_id):
        return self.items.get(item_id)
    async def list_children(self, parent_id):
        return [item for item in self.items.values() if item.parent_id == parent_id]
    async def create_folder(self, parent_id, name):
        self.counter += 1
        item = StorageItem(f"f{self.counter}", name, parent_id, "folder")
        self.items[item.id] = item
        return item
    async def upload_bytes(self, parent_id, name, mime_type, content):
        self.counter += 1
        item = StorageItem(f"file{self.counter}", name, parent_id, "file")
        self.items[item.id] = item
        self.uploads.append((item, content))
        return item
    async def rename_item(self, item_id, name):
        old = self.items[item_id]
        item = StorageItem(old.id, name, old.parent_id, old.kind)
        self.items[item.id] = item
        return item
    async def delete_item(self, item_id):
        self.items.pop(item_id, None)


def make_listing(sessions, platform="etsy"):
    with sessions.begin() as session:
        group = _group(session)
        group.platform = platform
        listing = _listing(session, group)
        return listing.id


def test_pipeline_structure_is_idempotent_and_platform_layout():
    engine, sessions = _session()
    try:
        listing_id = make_listing(sessions)
        with sessions.begin() as session:
            listing = session.get(ListingTaskModel, listing_id)
            storage = FakeStorage([StorageItem("listing-folder", "listing - 1", None, "folder")])
            pipeline = asyncio.run(ensure_pipeline_structure(listing, storage))
            again = asyncio.run(ensure_pipeline_structure(listing, storage))
            assert pipeline.id == again.id
            names = {item.name for item in storage.items.values() if item.parent_id == pipeline.id}
            assert {"Input", "Idea Story", "Prompt", "Generating", "Video Output", "Watermark & Smart Enhance", "Logs"} <= names
            assert {"1x1"} <= {item.name for item in storage.items.values() if item.parent_id in {x.id for x in storage.items.values() if x.name == "Video Output"}}
    finally:
        engine.dispose()


def test_pipeline_identity_validation_and_ambiguous_state():
    engine, sessions = _session()
    try:
        listing_id = make_listing(sessions)
        with sessions.begin() as session:
            listing = session.get(ListingTaskModel, listing_id)
            storage = FakeStorage([StorageItem("listing-folder", "listing - 1", None, "folder"), StorageItem("bad", "Pipeline", "other", "folder")])
            listing.pipeline_folder_id = "bad"
            with pytest.raises(PipelineStorageError):
                asyncio.run(ensure_pipeline_structure(listing, storage))
            listing.pipeline_folder_id = None
            storage.items["p2"] = StorageItem("p2", "Pipeline", "listing-folder", "folder")
            storage.items["p3"] = StorageItem("p3", "Pipeline", "listing-folder", "folder")
            with pytest.raises(PipelineStorageAmbiguous):
                asyncio.run(ensure_pipeline_structure(listing, storage))
    finally:
        engine.dispose()


def test_artifact_reservation_canonical_hash_and_collision():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(id="run-art", tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run); session.flush()
            service = ArtifactService(session)
            a = service.reserve_artifact(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=None, artifact_type=ArtifactType.INPUT_SNAPSHOT)
            b = service.reserve_artifact(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=None, artifact_type=ArtifactType.INPUT_SNAPSHOT)
            assert a.id == b.id
            storage = FakeStorage([StorageItem("listing-folder", "listing - 1", None, "folder"), StorageItem("input", "Input", "pipeline", "folder")])
            a._artifact_parent_id = "input"
            payload = {"b": 2, "a": 1}
            asyncio.run(service.materialize_json(a, storage, payload))
            expected = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()
            assert a.status == "available" and a.content_hash == expected and a.size_bytes == 13
            assert a.mime_type == "application/json"
            assert storage.uploads
    finally:
        engine.dispose()


def test_nullable_ratio_versions_are_unique():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(id="run-unique", tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run); session.flush()
            service = ArtifactService(session)
            service.reserve_artifact(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=None, artifact_type=ArtifactType.INPUT_MANIFEST)
            with pytest.raises(IntegrityError):
                session.add(ArtifactModel(tenant_id="tenant-a", listing_task_id=listing.id, pipeline_run_id=run.id, node_run_id=None, artifact_type="input_manifest", version=1, relative_path="Pipeline/Input/x", metadata_json={}))
                session.flush()
    finally:
        engine.dispose()
