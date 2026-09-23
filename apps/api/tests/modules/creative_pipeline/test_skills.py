from datetime import datetime, timezone

import pytest

from app.modules.creative_pipeline.model import (
    CreativeSkillBindingModel,
    CreativeSkillExecutionModel,
    CreativeSkillModel,
    CreativeSkillVersionModel,
    NodeRunModel,
    PipelineRunModel,
)
from app.modules.creative_pipeline.skill_executor import GPTSkillExecutor
from app.modules.creative_pipeline.skill_registry import CreativeSkillRegistry, CreativeSkillRegistryError
from tests.modules.creative_pipeline.test_domain import _group, _listing, _session


def _skill_session():
    engine, sessions = _session()
    for table in (
        CreativeSkillModel.__table__,
        CreativeSkillVersionModel.__table__,
        CreativeSkillBindingModel.__table__,
        CreativeSkillExecutionModel.__table__,
    ):
        table.create(engine, checkfirst=True)
    return engine, sessions


def _version(registry, *, tenant_id, skill_id, instructions):
    return registry.create_version(
        tenant_id=tenant_id,
        skill_id=skill_id,
        instructions=instructions,
        actor_id="tester",
        status="published",
    )


def test_skill_resolution_precedence_and_tenant_version_isolation():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            group = _group(session)
            listing = _listing(session, group)
            registry = CreativeSkillRegistry(session)

            system = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            assert system.binding_scope == "system_default"
            assert system.version == 1

            tenant_version = _version(
                registry,
                tenant_id="tenant-a",
                skill_id=system.skill_id,
                instructions="Tenant idea instructions",
            )
            registry.set_binding(
                tenant_id="tenant-a",
                node_type="idea_story",
                skill_version_id=tenant_version.id,
                scope_type="tenant",
                scope_id=None,
                actor_id="tester",
            )
            tenant_resolved = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            assert tenant_resolved.binding_scope == "tenant"
            assert tenant_resolved.skill_version_id == tenant_version.id
            idea_catalog = next(
                item
                for item in registry.list_skills("tenant-a")
                if item["node_type"] == "idea_story"
            )
            assert idea_catalog["effective_tenant_version_id"] == tenant_version.id
            assert idea_catalog["effective_tenant_scope"] == "tenant"
            compact_catalog = next(
                item
                for item in registry.list_skills(
                    "tenant-a",
                    include_content=False,
                )
                if item["node_type"] == "idea_story"
            )
            assert "instructions" not in compact_catalog["versions"][0]
            assert "input_schema" not in compact_catalog["versions"][0]
            assert "output_schema" not in compact_catalog["versions"][0]

            group_version = _version(
                registry,
                tenant_id="tenant-a",
                skill_id=system.skill_id,
                instructions="Group idea instructions",
            )
            registry.set_binding(
                tenant_id="tenant-a",
                node_type="idea_story",
                skill_version_id=group_version.id,
                scope_type="source_group",
                scope_id=group.id,
                actor_id="tester",
            )
            group_resolved = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            assert group_resolved.binding_scope == "source_group"
            assert group_resolved.skill_version_id == group_version.id

            listing_version = _version(
                registry,
                tenant_id="tenant-a",
                skill_id=system.skill_id,
                instructions="Listing idea instructions",
            )
            registry.set_binding(
                tenant_id="tenant-a",
                node_type="idea_story",
                skill_version_id=listing_version.id,
                scope_type="listing",
                scope_id=listing.id,
                actor_id="tester",
            )
            listing_resolved = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            assert listing_resolved.binding_scope == "listing"
            assert listing_resolved.skill_version_id == listing_version.id

            assert registry.visible_version("tenant-b", listing_version.id) is None
    finally:
        engine.dispose()


def test_skill_execution_snapshots_version_and_variant():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(
                id="skill-run",
                tenant_id="tenant-a",
                listing_task_id=listing.id,
                run_number=1,
                trigger_type="manual",
            )
            session.add(run)
            session.flush()
            node = NodeRunModel(
                id="skill-node",
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_type="prompt",
                status="running",
                attempt_count=1,
            )
            session.add(node)
            session.flush()

            registry = CreativeSkillRegistry(session)
            skill = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="prompt",
            )
            executor = GPTSkillExecutor(session)
            row = executor.start(
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_run_id=node.id,
                listing_task_id=listing.id,
                attempt_number=1,
                skill=skill,
                variant_key="seedance",
                idempotency_key="skill-exec-key",
                input_artifact_ids=["idea-artifact"],
                knowledge_snapshot_id="sha256:test",
                prompt_sha256="sha256:prompt",
                details={"target_provider": "seedance"},
            )
            same = executor.start(
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_run_id=node.id,
                listing_task_id=listing.id,
                attempt_number=1,
                skill=skill,
                variant_key="seedance",
                idempotency_key="skill-exec-key",
                input_artifact_ids=["idea-artifact"],
                knowledge_snapshot_id="sha256:test",
                prompt_sha256="sha256:prompt",
            )
            assert same.id == row.id

            executor.complete(
                row,
                provider="openai",
                model="gpt-test",
                provider_request_id="request-1",
                usage={"input_tokens": 100, "output_tokens": 50},
                output_artifact_ids=["prompt-artifact"],
            )
            summary = executor.summary(row)
            assert summary["status"] == "completed"
            assert summary["skill_version"] == 1
            assert summary["binding_scope"] == "system_default"
            assert summary["variant_key"] == "seedance"
            assert summary["input_artifact_ids"] == ["idea-artifact"]
            assert summary["output_artifact_ids"] == ["prompt-artifact"]
            assert summary["usage"]["input_tokens"] == 100
            assert row.completed_at is not None
    finally:
        engine.dispose()


def test_effective_listing_skills_keep_knowledge_refs_separate():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            items = CreativeSkillRegistry(session).effective_for_listing(
                tenant_id="tenant-a",
                listing=listing,
            )
            by_node = {item["node_type"]: item for item in items}
            assert by_node["idea_story"]["knowledge_refs"] == ["idea_story"]
            assert by_node["prompt"]["knowledge_refs"] == [
                "seedance_2_5",
                "google_omni",
            ]
            assert all(item["executor_type"] == "gpt_skill" for item in items)
    finally:
        engine.dispose()

def test_retry_keeps_original_skill_version_after_binding_changes():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(
                id="retry-skill-run",
                tenant_id="tenant-a",
                listing_task_id=listing.id,
                run_number=1,
                trigger_type="manual",
            )
            session.add(run)
            session.flush()
            node = NodeRunModel(
                id="retry-skill-node",
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_type="idea_story",
                status="running",
                attempt_count=1,
            )
            session.add(node)
            session.flush()

            registry = CreativeSkillRegistry(session)
            original = registry.resolve_for_node(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
                node_run_id=node.id,
            )
            executor = GPTSkillExecutor(session)
            executor.start(
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_run_id=node.id,
                listing_task_id=listing.id,
                attempt_number=1,
                skill=original,
                variant_key="default",
                idempotency_key="retry-skill-key",
                input_artifact_ids=["input-artifact"],
                knowledge_snapshot_id="sha256:test",
                prompt_sha256="sha256:prompt-v1",
            )
            executor.fail_running_for_node(
                tenant_id="tenant-a",
                node_run_id=node.id,
                error_code="temporary",
                error_message="temporary failure",
            )

            newer = _version(
                registry,
                tenant_id="tenant-a",
                skill_id=original.skill_id,
                instructions="New listing instructions",
            )
            registry.set_binding(
                tenant_id="tenant-a",
                node_type="idea_story",
                skill_version_id=newer.id,
                scope_type="listing",
                scope_id=listing.id,
                actor_id="tester",
            )

            current_binding = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            retry_binding = registry.resolve_for_node(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
                node_run_id=node.id,
            )
            assert current_binding.skill_version_id == newer.id
            assert retry_binding.skill_version_id == original.skill_version_id
            assert retry_binding.binding_scope == original.binding_scope
    finally:
        engine.dispose()


def test_skill_execution_lookup_is_tenant_and_node_scoped_and_terminal_slots_are_immutable():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            run = PipelineRunModel(
                id="scope-skill-run",
                tenant_id="tenant-a",
                listing_task_id=listing.id,
                run_number=1,
                trigger_type="manual",
            )
            session.add(run)
            session.flush()
            node = NodeRunModel(
                id="scope-skill-node",
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_type="prompt",
                status="running",
                attempt_count=1,
            )
            session.add(node)
            session.flush()

            skill = CreativeSkillRegistry(session).resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="prompt",
            )
            executor = GPTSkillExecutor(session)
            row = executor.start(
                tenant_id="tenant-a",
                pipeline_run_id=run.id,
                node_run_id=node.id,
                listing_task_id=listing.id,
                attempt_number=1,
                skill=skill,
                variant_key="seedance",
                idempotency_key="scope-key",
                input_artifact_ids=["idea-artifact"],
                knowledge_snapshot_id="sha256:test",
                prompt_sha256="sha256:prompt",
            )

            assert (
                executor.get(
                    tenant_id="tenant-a",
                    execution_id=row.id,
                    node_run_id=node.id,
                )
                is row
            )
            assert (
                executor.get(
                    tenant_id="tenant-b",
                    execution_id=row.id,
                    node_run_id=node.id,
                )
                is None
            )
            assert (
                executor.get(
                    tenant_id="tenant-a",
                    execution_id=row.id,
                    node_run_id="different-node",
                )
                is None
            )

            executor.complete(
                row,
                provider="openai",
                model="gpt-test",
                provider_request_id="request-2",
                usage={},
                output_artifact_ids=["prompt-artifact"],
            )
            with pytest.raises(ValueError, match="skill_execution_output_inconsistent"):
                executor.start(
                    tenant_id="tenant-a",
                    pipeline_run_id=run.id,
                    node_run_id=node.id,
                    listing_task_id=listing.id,
                    attempt_number=1,
                    skill=skill,
                    variant_key="seedance",
                    idempotency_key="scope-key",
                    input_artifact_ids=["idea-artifact"],
                    knowledge_snapshot_id="sha256:test",
                    prompt_sha256="sha256:prompt",
                )
    finally:
        engine.dispose()

def test_skill_knowledge_refs_must_match_runtime_stage_contract():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            registry = CreativeSkillRegistry(session)
            skill = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            with pytest.raises(
                CreativeSkillRegistryError,
                match="invalid_skill_knowledge_refs",
            ):
                registry.create_version(
                    tenant_id="tenant-a",
                    skill_id=skill.skill_id,
                    instructions="Invalid extra knowledge reference",
                    actor_id="tester",
                    knowledge_refs=["idea_story", "seedance_2_5"],
                    status="published",
                )
    finally:
        engine.dispose()


def test_invalid_active_binding_fails_closed_instead_of_silently_falling_back():
    engine, sessions = _skill_session()
    try:
        with sessions.begin() as session:
            listing = _listing(session, _group(session))
            registry = CreativeSkillRegistry(session)
            system = registry.resolve(
                tenant_id="tenant-a",
                listing=listing,
                node_type="idea_story",
            )
            custom = _version(
                registry,
                tenant_id="tenant-a",
                skill_id=system.skill_id,
                instructions="Bound instructions",
            )
            registry.set_binding(
                tenant_id="tenant-a",
                node_type="idea_story",
                skill_version_id=custom.id,
                scope_type="listing",
                scope_id=listing.id,
                actor_id="tester",
            )
            custom.status = "archived"
            session.flush()

            with pytest.raises(
                CreativeSkillRegistryError,
                match="skill_binding_version_unavailable",
            ):
                registry.resolve(
                    tenant_id="tenant-a",
                    listing=listing,
                    node_type="idea_story",
                )
    finally:
        engine.dispose()