from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.assets.model import AssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.attention_recovery import PipelineAttentionRecovery
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import ProcessingJobModel


def sessions():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed(session, *, state: str, error_code: str, analysis_status: str = "failed"):
    registry = AssetRegistryRepository(session)
    source = registry.upsert_external_source(
        tenant_id="tenant-a",
        source_key="drive",
        source_type="onedrive",
    )
    source_asset = registry.upsert_source_asset(
        tenant_id="tenant-a",
        external_source_id=source.id,
        external_asset_id=f"remote-{state}-{error_code}",
        filename="photo.jpg",
        mime_type="image/jpeg",
    )
    asset = AssetModel(
        tenant_id="tenant-a",
        content_hash=("a" if state == "storage_failed" else "b") * 64,
        mime_type="image/jpeg",
        size_bytes=100,
    )
    session.add(asset)
    session.flush()
    profile = MetadataProfileModel(
        tenant_id="tenant-a",
        profile_name="default",
        profile_version="v1",
        prompt_template="Describe image",
        active=True,
    )
    session.add(profile)
    session.flush()
    analysis = AssetAiAnalysisModel(
        tenant_id="tenant-a",
        asset_id=asset.id,
        content_hash=asset.content_hash,
        metadata_profile_id=profile.id,
        metadata_profile=profile.profile_name,
        metadata_profile_version=profile.profile_version,
        prompt_version="auto-v1",
        pipeline_version="asset-pipeline-v1",
        ai_provider="gemini",
        status=analysis_status,
        last_error_code=error_code if analysis_status == "failed" else None,
    )
    session.add(analysis)
    session.flush()
    pipeline = AssetPipelineModel(
        tenant_id="tenant-a",
        correlation_id=f"pipeline-{state}-{error_code}",
        origin_type="source_asset",
        origin_id=source_asset.id,
        source_asset_id=source_asset.id,
        asset_id=asset.id,
        analysis_id=analysis.id,
        content_hash=asset.content_hash,
        state=state,
        last_error_code=error_code,
        last_error_message=error_code,
        failure_retryable=False,
    )
    session.add(pipeline)
    session.flush()
    return pipeline, analysis


def settings():
    return Settings(
        AI_ANALYSIS_SOURCE_FALLBACK_ENABLED=True,
        DYNAMIC_AI_METADATA_ENABLED=True,
        AI_AUTO_ANALYZE_ENABLED=True,
        AI_SINGLE_ANALYSIS_ENABLED=True,
        GEMINI_API_KEY="test-only",
    )


def test_storage_failure_is_recovered_to_source_analysis():
    engine, factory = sessions()
    try:
        with factory() as session:
            pipeline, analysis = seed(
                session,
                state="storage_failed",
                error_code="managed_storage_forbidden",
            )
            job = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_pipeline",
                entity_id=pipeline.id,
                idempotency_key=f"pipeline:{pipeline.id}:asset_analyze:{analysis.id}",
                payload_json={"pipeline_id": pipeline.id, "analysis_id": analysis.id},
                status="failed",
                attempt_count=5,
                max_attempts=5,
                last_error_code="managed_asset_storage_failed",
            )
            session.add(job)
            session.commit()

            result = PipelineAttentionRecovery(session, settings()).apply("tenant-a")
            session.commit()

            assert result.storage_pipelines == 1
            pipeline = session.get(AssetPipelineModel, pipeline.id)
            job = session.get(ProcessingJobModel, job.id)
            analysis = session.get(AssetAiAnalysisModel, analysis.id)
            assert pipeline.state == "analysis_pending"
            assert pipeline.last_error_code is None
            assert job.status == "retry"
            assert job.payload_json["analysis_content_source"] == "source_asset"
            assert job.max_attempts >= 8
            assert analysis.status == "pending"
    finally:
        engine.dispose()


def test_gemini_terminal_failure_retries_from_source():
    engine, factory = sessions()
    try:
        with factory() as session:
            pipeline, analysis = seed(
                session,
                state="analysis_failed",
                error_code="gemini_invalid_json",
            )
            job = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_pipeline",
                entity_id=pipeline.id,
                idempotency_key=f"pipeline:{pipeline.id}:asset_analyze:{analysis.id}",
                payload_json={"pipeline_id": pipeline.id, "analysis_id": analysis.id},
                status="failed",
                attempt_count=3,
                max_attempts=3,
                last_error_code="gemini_invalid_json",
            )
            session.add(job)
            session.commit()

            result = PipelineAttentionRecovery(session, settings()).apply("tenant-a")
            session.commit()

            assert result.analysis_jobs == 1
            assert session.get(AssetPipelineModel, pipeline.id).state == "analysis_pending"
            repaired = session.get(ProcessingJobModel, job.id)
            assert repaired.status == "retry"
            assert repaired.payload_json["analysis_content_source"] == "source_asset"
    finally:
        engine.dispose()


def test_projection_failure_retries_only_with_completed_analysis():
    engine, factory = sessions()
    try:
        with factory() as session:
            pipeline, analysis = seed(
                session,
                state="projection_failed",
                error_code="ValueError",
                analysis_status="completed",
            )
            job = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="search_projection_build",
                entity_type="asset_pipeline",
                entity_id=pipeline.id,
                idempotency_key=f"pipeline:{pipeline.id}:search_projection_build:{analysis.id}:None:None",
                payload_json={"pipeline_id": pipeline.id},
                status="failed",
                last_error_code="ValueError",
                last_error_message="no completed analysis is available",
            )
            session.add(job)
            session.commit()

            result = PipelineAttentionRecovery(session, settings()).apply("tenant-a")
            session.commit()

            assert result.projection_jobs == 1
            assert session.get(AssetPipelineModel, pipeline.id).state == "projection_pending"
            assert session.get(ProcessingJobModel, job.id).status == "retry"
    finally:
        engine.dispose()


def test_apply_requires_source_fallback():
    engine, factory = sessions()
    try:
        with factory() as session:
            recovery = PipelineAttentionRecovery(
                session,
                Settings(AI_ANALYSIS_SOURCE_FALLBACK_ENABLED=False),
            )
            try:
                recovery.apply("tenant-a")
            except RuntimeError as exc:
                assert str(exc) == "source_analysis_fallback_disabled"
            else:
                raise AssertionError("expected source fallback guard")
    finally:
        engine.dispose()
