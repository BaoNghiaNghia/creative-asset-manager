import asyncio

from sqlalchemy import select

from app.core.config import Settings
from app.modules.creative_pipeline.discovery import CreativePipelineDiscoveryScanner
from app.modules.creative_pipeline.model import ListingTaskModel, PipelineRunModel
from app.modules.creative_pipeline.rollout import CreativePipelineRolloutPolicy
from tests.modules.creative_pipeline.test_discovery import FakeGateway, entry
from tests.modules.creative_pipeline.test_domain import _session


def _policy(**values):
    base = {
        "CREATIVE_PIPELINE_ROLLOUT_ENABLED": True,
        "CREATIVE_PIPELINE_ROLLOUT_TENANT_IDS": "tenant-a",
        "CREATIVE_PIPELINE_ROLLOUT_EXTERNAL_SOURCE_IDS": "source-a",
        "CREATIVE_PIPELINE_ROLLOUT_SOURCE_GROUP_FOLDER_IDS": "group-a",
        "CREATIVE_PIPELINE_ROLLOUT_LISTING_FOLDER_IDS": "",
        "CREATIVE_PIPELINE_ROLLOUT_MAX_ACTIVE_RUNS": 1,
    }
    base.update(values)
    return CreativePipelineRolloutPolicy.from_settings(Settings(**base))


def test_rollout_is_deny_by_default_and_requires_exact_tenant_source_and_group():
    disabled = CreativePipelineRolloutPolicy.from_settings(Settings())
    assert disabled.reason_for_listing(
        tenant_id="tenant-a", external_source_id="source-a",
        source_group_folder_id="group-a", listing_folder_id="listing-a",
    ) == "creative_pipeline_rollout_disabled"

    policy = _policy()
    assert policy.allows_listing(
        tenant_id="tenant-a", external_source_id="source-a",
        source_group_folder_id="group-a", listing_folder_id="listing-a",
    )
    assert policy.reason_for_listing(
        tenant_id="tenant-b", external_source_id="source-a",
        source_group_folder_id="group-a", listing_folder_id="listing-a",
    ) == "creative_pipeline_rollout_tenant_denied"
    assert policy.reason_for_listing(
        tenant_id="tenant-a", external_source_id="source-b",
        source_group_folder_id="group-a", listing_folder_id="listing-a",
    ) == "creative_pipeline_rollout_source_denied"
    assert policy.reason_for_listing(
        tenant_id="tenant-a", external_source_id="source-a",
        source_group_folder_id="group-b", listing_folder_id="listing-a",
    ) == "creative_pipeline_rollout_group_denied"


def test_listing_allowlist_narrows_an_authorized_group():
    policy = _policy(CREATIVE_PIPELINE_ROLLOUT_LISTING_FOLDER_IDS="listing-a")
    assert policy.allows_listing(
        tenant_id="tenant-a", external_source_id="source-a",
        source_group_folder_id="group-a", listing_folder_id="listing-a",
    )
    assert policy.reason_for_listing(
        tenant_id="tenant-a", external_source_id="source-a",
        source_group_folder_id="group-a", listing_folder_id="listing-b",
    ) == "creative_pipeline_rollout_listing_denied"


def test_canary_discovery_keeps_listing_inventory_but_only_creates_allowed_runs():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({
            "root": [entry("group-a", "Etsy - Allowed"), entry("group-b", "Amazon - Denied")],
            "group-a": [entry("listing-a", "listing - 1"), entry("listing-b", "listing - 2")],
            "group-b": [entry("listing-c", "listing - 3")],
        })
        policy = _policy(CREATIVE_PIPELINE_ROLLOUT_LISTING_FOLDER_IDS="listing-a")
        with sessions() as session:
            result = asyncio.run(CreativePipelineDiscoveryScanner(
                session, rollout_policy=policy,
            ).scan(
                tenant_id="tenant-a", external_source_id="source-a",
                root_folder_id="root", gateway=gateway,
            ))
        with sessions() as session:
            assert len(session.scalars(select(ListingTaskModel)).all()) == 3
            runs = list(session.scalars(select(PipelineRunModel)).all())
        assert result.pipeline_runs_created == 1
        assert len(runs) == 1
        assert runs[0].listing_task_id
    finally:
        engine.dispose()



def test_previously_discovered_listing_gets_one_run_when_later_allowlisted():
    engine, sessions = _session()
    try:
        gateway = FakeGateway({
            "root": [entry("group-a", "Etsy - Allowed")],
            "group-a": [entry("listing-a", "listing - 1")],
        })
        disabled = CreativePipelineRolloutPolicy.from_settings(Settings())
        with sessions() as session:
            first = asyncio.run(CreativePipelineDiscoveryScanner(
                session, rollout_policy=disabled,
            ).scan(
                tenant_id="tenant-a", external_source_id="source-a",
                root_folder_id="root", gateway=gateway,
            ))
        assert first.pipeline_runs_created == 0
        with sessions() as session:
            second = asyncio.run(CreativePipelineDiscoveryScanner(
                session, rollout_policy=_policy(CREATIVE_PIPELINE_ROLLOUT_LISTING_FOLDER_IDS="listing-a"),
            ).scan(
                tenant_id="tenant-a", external_source_id="source-a",
                root_folder_id="root", gateway=gateway,
            ))
        with sessions() as session:
            assert len(session.scalars(select(PipelineRunModel)).all()) == 1
        assert second.pipeline_runs_created == 1
    finally:
        engine.dispose()



def test_scheduler_uses_exact_rollout_source_when_enabled():
    from datetime import datetime, timezone

    from app.modules.creative_pipeline.canary import CreativePipelineCanaryScheduler
    from app.modules.processing.model import ProcessingJobModel
    from app.modules.processing_policy.model import TenantProcessingPolicyModel

    engine, sessions = _session()
    try:
        ProcessingJobModel.__table__.create(engine, checkfirst=True)
        TenantProcessingPolicyModel.__table__.create(engine, checkfirst=True)
        with sessions.begin() as session:
            for source in session.scalars(select(__import__(
                "app.modules.assets.model", fromlist=["ExternalSourceModel"]
            ).ExternalSourceModel)).all():
                source.source_type = "google_drive"
                source.status = "active"
        settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            CREATIVE_PIPELINE_CANARY_ENABLED=True,
            CREATIVE_PIPELINE_CANARY_ROOT_FOLDER_ID="root",
            AUTH_DEFAULT_TENANT_ID="tenant-a",
            CREATIVE_PIPELINE_ROLLOUT_ENABLED=True,
            CREATIVE_PIPELINE_ROLLOUT_TENANT_IDS="tenant-a",
            CREATIVE_PIPELINE_ROLLOUT_EXTERNAL_SOURCE_IDS="source-a",
            CREATIVE_PIPELINE_ROLLOUT_SOURCE_GROUP_FOLDER_IDS="group-a",
        )
        job = CreativePipelineCanaryScheduler(sessions, settings).tick(
            now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
        )
        assert job is not None
        assert job.payload_json["external_source_id"] == "source-a"
    finally:
        engine.dispose()
