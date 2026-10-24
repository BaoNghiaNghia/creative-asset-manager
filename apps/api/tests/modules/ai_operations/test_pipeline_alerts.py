from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.ai_operations.pipeline import PipelineOperationsRepository
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.processing.model import ProcessingJobModel


def test_retrying_download_is_waiting_not_actionable_and_uses_real_failure_time():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    old = datetime.now(timezone.utc) - timedelta(days=2)
    with Session(engine) as session:
        source = ExternalSourceModel(
            tenant_id="tenant-a", source_key="onedrive-test",
            source_type="onedrive", status="active", source_metadata={},
        )
        session.add(source)
        session.flush()
        item = SourceAssetModel(
            tenant_id="tenant-a", external_source_id=source.id,
            external_asset_id="item-1", filename="example.png",
            mime_type="image/png", source_metadata={},
        )
        session.add(item)
        session.flush()
        pipeline = AssetPipelineModel(
            tenant_id="tenant-a", correlation_id="pipeline-one",
            origin_type="source_asset", origin_id=item.id,
            source_asset_id=item.id, state="download_failed",
            last_error_code="pipeline_content_transient",
            updated_at=old,
        )
        session.add(pipeline)
        job = ProcessingJobModel(
            tenant_id="tenant-a", job_type="source_asset_download",
            entity_type="source_asset", entity_id=item.id,
            idempotency_key="source-retry", payload_json={},
            status="retry", priority=15, attempt_count=1, max_attempts=5,
            next_attempt_at=old,
            last_error_code="pipeline_content_transient",
            updated_at=old,
        )
        session.add(job)
        session.commit()

        repository = PipelineOperationsRepository(session)
        snapshot = repository.snapshot("tenant-a")
        assert snapshot["failure_groups"] == []
        download = next(s for s in snapshot["stages"] if s["key"] == "source_asset_download")
        assert download["waiting_assets"] == 1
        assert download["needs_attention_assets"] == 0

        # An exhausted, truly failed job still warrants operator attention.
        job.status = "failed"
        job.updated_at = old  # Preserve the historical failure timestamp.
        session.flush()
        snapshot = repository.snapshot("tenant-a")
        failures = snapshot["failure_groups"]
        assert len(failures) == 1
        assert failures[0]["error_code"] == "pipeline_content_transient"
        assert failures[0]["count"] == 1
        assert failures[0]["latest_at"].date() == old.date()
    engine.dispose()
