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
from app.modules.creative_pipeline.artifacts import ArtifactService
from app.modules.creative_pipeline.lineage import CreativePipelineLineageError, CreativePipelineLineageResolver
from app.modules.creative_pipeline.api_service import CreativePipelineApiService
from app.modules.creative_pipeline.router import groups as list_groups, listings as list_listings
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.authorization.folder_scope import FolderScopeAccess


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    names = (
        "tenants",
        "oauth_connections",
        "external_sources",
        "creative_pipeline_source_groups",
        "creative_pipeline_listing_tasks",
        "creative_pipeline_runs",
        "creative_pipeline_node_runs",
        "creative_pipeline_generation_runs",
        "creative_pipeline_artifacts",
        "creative_pipeline_skills",
        "creative_pipeline_skill_versions",
        "creative_pipeline_skill_bindings",
        "creative_pipeline_skill_executions",
    )
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
            session.add(GenerationRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, listing_task_id=listing.id, provider="seedance", model="seedance-2.5", aspect_ratio="1:1", generation_number=1))
            session.flush()
            with pytest.raises(IntegrityError):
                session.add(GenerationRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, listing_task_id=listing.id, provider="seedance", model="seedance-2.5", aspect_ratio="1:1", generation_number=1))
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
            generation = GenerationRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, listing_task_id=listing.id, provider="seedance", model="seedance-2.5", aspect_ratio=ratio, generation_number=1)
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

def test_input_bundle_allocator_is_retry_stable():
    engine, sessions = _session()
    try:
        session = sessions()
        listing = _listing(session, _group(session))
        run = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="manual")
        node = NodeRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, node_type=NodeType.INPUT_DATA.value, status=NodeRunStatus.READY.value)
        session.add(run); session.flush(); session.add(node); session.flush()
        service = ArtifactService(session)
        first_version, first = service.reserve_input_bundle(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=node.id)
        second_version, second = service.reserve_input_bundle(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=node.id)
        assert first_version == second_version == 1
        assert {key: value.id for key, value in first.items()} == {key: value.id for key, value in second.items()}
        session.commit()
    finally:
        engine.dispose()


def test_lineage_resolver_prefers_parent_and_rejects_cycles():
    engine, sessions = _session()
    try:
        session = sessions()
        listing = _listing(session, _group(session))
        parent = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="manual")
        session.add(parent); session.flush()
        node = NodeRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=parent.id, node_type=NodeType.INPUT_DATA.value, status=NodeRunStatus.READY.value)
        session.add(node); session.flush()
        artifact = ArtifactService(session).reserve_artifact(tenant_id="tenant-a", pipeline_run_id=parent.id, node_run_id=node.id, artifact_type=ArtifactType.INPUT_SNAPSHOT, version=1)
        artifact.status = "available"
        child = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, parent_run_id=parent.id, branch_start_node=NodeType.IDEA_STORY.value, run_number=2, trigger_type="regenerate")
        session.add(child); session.flush()
        resolver = CreativePipelineLineageResolver(session)
        assert resolver.effective_input_snapshot(child).id == artifact.id
        child.parent_run_id = child.id
        with pytest.raises(CreativePipelineLineageError): resolver.ancestors(child)
        session.rollback()
    finally:
        engine.dispose()


def test_prompt_bundle_partial_retry_preserves_version_and_identity():
    engine, sessions = _session()
    try:
        session = sessions()
        listing = _listing(session, _group(session))
        run = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id, run_number=1, trigger_type="manual")
        session.add(run); session.flush()
        node = NodeRunModel(id=str(uuid4()), tenant_id="tenant-a", pipeline_run_id=run.id, node_type=NodeType.PROMPT.value, status=NodeRunStatus.READY.value)
        session.add(node); session.flush()
        service = ArtifactService(session)
        version, first = service.reserve_prompt_bundle(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=node.id)
        first["seedance"].status = "available"
        retry_version, retry = service.reserve_prompt_bundle(tenant_id="tenant-a", pipeline_run_id=run.id, node_run_id=node.id)
        assert retry_version == version == 1
        assert retry["seedance"].id == first["seedance"].id
        assert retry["google_omni"].id == first["google_omni"].id
        session.commit()
    finally:
        engine.dispose()


def test_group_summary_uses_only_latest_run_and_marks_inherited_nodes():
    engine, sessions = _session()
    try:
        session = sessions()
        group = _group(session)
        listing_a = _listing(session, group, listing_key="a")
        listing_b = _listing(session, group, folder="listing-b", listing_key="b")
        runs = [PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing_a.id, run_number=1, status="completed", trigger_type="manual"), PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing_a.id, run_number=2, status="failed", trigger_type="manual"), PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing_a.id, run_number=3, status="running", trigger_type="manual"), PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing_b.id, run_number=1, status="completed", trigger_type="manual")]
        session.add_all(runs); session.flush()
        child = PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing_b.id, parent_run_id=runs[-1].id, run_number=2, status="queued", trigger_type="regenerate", branch_start_node=NodeType.PROMPT.value)
        session.add(child); session.flush()
        summary = CreativePipelineApiService(session).group_summary(group)
        assert summary["current_runs"]["running"] == 1
        assert summary["current_runs"]["completed"] == 0
        nodes = CreativePipelineApiService(session).node_summary(child)
        assert next(row for row in nodes if row["node_type"] == NodeType.INPUT_DATA.value)["inherited"] is True
        assert next(row for row in nodes if row["node_type"] == NodeType.PROMPT.value)["inherited"] is False
        session.commit()
    finally:
        engine.dispose()


def test_group_and_listing_reads_are_batched_and_tenant_scoped():
    engine, sessions = _session()
    try:
        session = sessions()
        principal = CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="member-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset({"assets.read"}), platform_admin=False,
            session_id="session-a", authorization_source="test",
        )
        for group_index in range(3):
            group = _group(session, folder=f"group-{group_index}")
            for listing_index in range(10):
                listing = _listing(
                    session, group, folder=f"folder-{group_index}-{listing_index}",
                    listing_key=f"key-{group_index}-{listing_index}",
                )
                session.add_all([
                    PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id,
                                     run_number=1, status="completed", trigger_type="manual"),
                    PipelineRunModel(id=str(uuid4()), tenant_id="tenant-a", listing_task_id=listing.id,
                                     run_number=2, status="failed", trigger_type="manual"),
                ])
        foreign_group = _group(session, tenant="tenant-b", source="source-b", folder="foreign-group")
        _listing(session, foreign_group, folder="foreign-listing", listing_key="foreign")
        session.flush()
        sql = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                sql.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            group_result = list_groups(session=session, principal=principal)
            group_queries = len(sql)
            sql.clear()
            listing_result = list_listings(
                q=None, source_group_id=None, platform=None, source_status=None,
                run_status=None, page=1, limit=5, session=session, principal=principal,
            )
            listing_queries = len(sql)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert group_result["total"] == 3
        assert all(item["listing_count"] == 10 for item in group_result["items"])
        assert all(item["current_runs"]["failed"] == 10 for item in group_result["items"])
        assert listing_result["total"] == 30
        assert len(listing_result["items"]) == 5
        assert all(item["pipeline_status"] == "failed" for item in listing_result["items"])
        assert all(item["current_run"]["nodes"] for item in listing_result["items"])
        assert (group_queries, listing_queries) == (3, 6)
        filtered = list_listings(
            q=None, source_group_id=None, platform=None, source_status=None,
            run_status="failed", page=2, limit=5, session=session, principal=principal,
        )
        assert filtered["total"] == 30 and len(filtered["items"]) == 5
        hidden = list_listings(
            q=None, source_group_id=foreign_group.id, platform=None, source_status=None,
            run_status=None, page=1, limit=5, session=session, principal=principal,
        )
        assert hidden["total"] == 0 and hidden["items"] == []
    finally:
        engine.dispose()


def test_cached_viewer_scope_access_still_checks_each_listing_folder(monkeypatch):
    engine, sessions = _session()
    try:
        session = sessions()
        group = _group(session)
        allowed = _listing(session, group, folder="allowed", listing_key="allowed")
        denied = _listing(session, group, folder="denied", listing_key="denied")
        principal = CurrentPrincipal(
            user_id="viewer-a", active_tenant_id="tenant-a", membership_id="member-a",
            external_identity=None, effective_roles=frozenset({"viewer"}),
            effective_permissions=frozenset({"assets.read"}), platform_admin=False,
            session_id="session-a", authorization_source="test",
        )
        service = CreativePipelineApiService(session)
        accesses = []

        def access(**kwargs):
            accesses.append(kwargs)
            return FolderScopeAccess(True, "source-a", frozenset({"allowed"}))

        monkeypatch.setattr(service._folder_scope, "access", access)
        monkeypatch.setattr(service._folder_scope, "allows_external_asset", lambda **kwargs: kwargs["external_asset_id"] == "allowed")
        assert service._scope_allows(principal, group, allowed)
        assert not service._scope_allows(principal, group, denied)
        assert len(accesses) == 1
    finally:
        engine.dispose()


def test_branch_run_creation_is_idempotent_after_listing_lock(monkeypatch):
    class _Orchestrator:
        def __init__(self, session):
            self.session = session
        def initialize_branch(self, *args):
            return []
        def schedule_ready_nodes(self, *args):
            return []
    monkeypatch.setattr(
        "app.modules.creative_pipeline.api_service.CreativePipelineOrchestrator",
        _Orchestrator,
    )
    engine, sessions = _session()
    try:
        session = sessions()
        listing = _listing(session, _group(session))
        principal = CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="member-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset(), platform_admin=False,
            session_id="session-a", authorization_source="test",
        )
        service = CreativePipelineApiService(session)
        first = service.create_branch_run(principal, listing, "run", "operator-key")
        repeated = service.create_branch_run(principal, listing, "run", "operator-key")
        assert repeated.id == first.id
        assert first.run_number == 1
        assert len(session.scalars(select(PipelineRunModel).where(
            PipelineRunModel.tenant_id == "tenant-a",
            PipelineRunModel.listing_task_id == listing.id,
        )).all()) == 1
    finally:
        engine.dispose()