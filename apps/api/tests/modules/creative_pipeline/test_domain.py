from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.creative_pipeline.constants import ArtifactType, CreativePlatform, NodeRunStatus, NodeType
from app.modules.creative_pipeline.model import ArtifactModel, GenerationRunModel, ListingTaskModel, NodeRunModel, PipelineRunModel, SourceGroupModel
from app.modules.creative_pipeline.repository import CreativePipelineRepository


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    names = ("tenants", "oauth_connections", "external_sources", "creative_pipeline_source_groups", "creative_pipeline_listing_tasks", "creative_pipeline_runs", "creative_pipeline_node_runs", "creative_pipeline_generation_runs", "creative_pipeline_artifacts")
    for name in names:
        Base.metadata.tables[name].create(engine, checkfirst=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add_all([TenantModel(id="tenant-a", name="A", slug="a"), TenantModel(id="tenant-b", name="B", slug="b")])
        session.add_all([
            ExternalSourceModel(id="source-a", tenant_id="tenant-a", source_key="source-a", source_type="etsy", source_metadata={}),
            ExternalSourceModel(id="source-b", tenant_id="tenant-b", source_key="source-b", source_type="etsy", source_metadata={}),
        ])
    return engine, sessions


def _group(session, tenant="tenant-a", source="source-a", folder="group-folder"):
    row = SourceGroupModel(id=str(uuid4()), tenant_id=tenant, platform="etsy", name="Etsy - Shop", source_provider="google_drive", external_source_id=source, external_folder_id=folder, source_path=f"/{folder}")
    session.add(row)
    session.flush()
    return row


def _listing(session, group, folder="listing-folder", listing_key="4527798886"):
    row = ListingTaskModel(id=str(uuid4()), tenant_id=group.tenant_id, source_group_id=group.id, platform=group.platform, listing_key=listing_key, folder_name=f"listing - {listing_key}", folder_path=f"/{folder}", external_folder_id=folder, first_discovered_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc))
    session.add(row)
    session.flush()
    return row


def test_source_group_stable_identity_and_cross_tenant_isolation():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            _group(session)
            with pytest.raises(IntegrityError):
                _group(session)
        with sessions.begin() as session:
            other = _group(session, tenant="tenant-b", source="source-b", folder="group-folder")
            assert other.tenant_id == "tenant-b"
    finally:
        engine.dispose()


def test_listing_identity_allows_same_key_across_groups_but_not_same_folder():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            group = _group(session)
            second = _group(session, folder="second-group")
            _listing(session, group, listing_key="same-key")
            _listing(session, second, folder="second-listing", listing_key="same-key")
            with pytest.raises(IntegrityError):
                _listing(session, group, folder="listing-folder", listing_key="different-key")
    finally:
        engine.dispose()


def test_pipeline_runs_and_generation_numbers_are_unique():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run)
            session.flush()
            session.add(PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=2, trigger_type="manual"))
            session.flush()
            session.add(GenerationRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, provider="seedance", model="seedance-2.5", aspect_ratio="1:1", generation_number=1))
            session.flush()
            with pytest.raises(IntegrityError):
                session.add(GenerationRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, provider="seedance", model="seedance-2.5", aspect_ratio="1:1", generation_number=1))
                session.flush()
    finally:
        engine.dispose()


def test_node_constraints_and_valid_types():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run)
            session.flush()
            node = NodeRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, node_type=NodeType.INPUT_DATA.value, status=NodeRunStatus.READY.value)
            session.add(node)
            session.flush()
            with pytest.raises(IntegrityError):
                session.add(NodeRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, node_type="not_a_node", status=NodeRunStatus.PENDING.value))
                session.flush()
    finally:
        engine.dispose()


@pytest.mark.parametrize("field,value", [("attempt_count", -1), ("max_attempts", 0)])
def test_node_retry_counters_rejected(field, value):
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run)
            session.flush()
            values = {"id": str(uuid4()), "tenant_id": "tenant-a", "pipeline_run_id": run.id, "node_type": "input_data", field: value}
            with pytest.raises(IntegrityError):
                session.add(NodeRunModel(**values))
                session.flush()
    finally:
        engine.dispose()


@pytest.mark.parametrize("ratio", ["1:1", "16:9", "9:16"])
def test_generation_aspect_ratios_and_artifact_lineage(ratio):
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="discovery")
            session.add(run)
            session.flush()
            generation = GenerationRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, provider="seedance", model="seedance-2.5", aspect_ratio=ratio, generation_number=1)
            session.add(generation)
            session.flush()
            artifact = ArtifactModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, pipeline_run_id=run.id, generation_run_id=generation.id, artifact_type=ArtifactType.RAW_VIDEO.value, version=1, relative_path=f"Pipeline/Video Output/{ratio.replace(':', 'x')}/v001.mp4", aspect_ratio=ratio, mime_type="video/mp4", metadata_json={})
            session.add(artifact)
            session.flush()
            assert artifact.generation_run_id == generation.id
    finally:
        engine.dispose()


def test_cross_tenant_child_fk_and_repository_scoping():
    engine, sessions = _session()
    try:
        with sessions.begin() as session:
            group = _group(session)
            with pytest.raises(IntegrityError):
                session.add(ListingTaskModel(id=str(uuid4()), tenant_id="tenant-b", source_group_id=group.id, platform="etsy", listing_key="x", folder_name="listing - x", folder_path="/x", external_folder_id="x", first_discovered_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc)))
                session.flush()
        with sessions.begin() as session:
            repo = CreativePipelineRepository(session)
            _group(session, tenant="tenant-a", source="source-a", folder="a2")
            _group(session, tenant="tenant-b", source="source-b", folder="b2")
            assert all(item.tenant_id == "tenant-a" for item in repo.list_source_groups("tenant-a"))
            assert repo.list_source_groups("tenant-b")
    finally:
        engine.dispose()