from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.domain.processing.handlers import JobHandlerResult
from app.infrastructure.search.elasticsearch_v2 import ElasticsearchV3Config
from app.modules.visual_search.elasticsearch import VisualSearchElasticsearchIndex
from app.modules.visual_search.index_handler import VisualIndexSyncJobHandler
from app.modules.visual_search.metrics import VisualSearchMetrics
from app.modules.visual_search.model_spec import VISUAL_SEARCH_V2_DESCRIPTOR


def _context(operation: str | None = None):
    return SimpleNamespace(
        job=SimpleNamespace(payload={"operation": operation} if operation else {}),
        logger=Mock(),
    )


@pytest.mark.parametrize(
    ("operation", "result", "expected"),
    [
        (None, JobHandlerResult.completed(), "success"),
        ("reconcile_retired_source", JobHandlerResult.completed(), "retired"),
        (None, JobHandlerResult.retryable("visual_index_failed", "failed"), "error"),
    ],
)
def test_index_completion_observes_once_and_logs_bounded_fields(
    operation, result, expected
):
    metrics = VisualSearchMetrics()
    context = _context(operation)
    handler = VisualIndexSyncJobHandler()

    def handle(_context, stages):
        stages.update(
            source_read_ms=1,
            prepare_image_ms=2,
            encode_ms=3,
            es_upsert_ms=4,
        )
        return result

    with patch.object(handler, "_handle", side_effect=handle), \
         patch("app.modules.visual_search.index_handler.VISUAL_SEARCH_METRICS", metrics):
        assert handler(context) == result

    snapshot = metrics.snapshot()
    assert snapshot["visual_index_jobs_total"] == [{"outcome": expected, "value": 1}]
    assert {row["stage"] for row in snapshot["index_latency"]} == {
        "job_total_ms", "source_read_ms", "prepare_image_ms", "encode_ms", "es_upsert_ms"
    }
    context.logger.info.assert_called_once()
    log_payload = context.logger.info.call_args.args[1]
    assert "tenant" not in log_payload
    assert "asset" not in log_payload


def test_stale_schema_job_is_non_retryable_before_asset_or_source_access() -> None:
    provider = VisualSearchElasticsearchIndex(
        ElasticsearchV3Config("http://elasticsearch.test"),
        VISUAL_SEARCH_V2_DESCRIPTOR,
    )
    context = SimpleNamespace(
        job=SimpleNamespace(
            tenant_id="tenant-a",
            payload={"embedding_schema_version": "visual_embedding_v1"},
        ),
        dependencies=SimpleNamespace(
            resources={
                "visual_index_provider": provider,
                "visual_encoder_client": object(),
                "visual_content_resolver": object(),
            }
        ),
        cancellation_requested=SimpleNamespace(is_set=lambda: False),
        shutdown_requested=SimpleNamespace(is_set=lambda: False),
    )
    handler = VisualIndexSyncJobHandler(
        SimpleNamespace(
            VISUAL_SEARCH_ENABLED=True,
            ELASTICSEARCH_URL="http://elasticsearch.test",
            VISUAL_SEARCH_TENANT_ALLOWLIST="tenant-a",
        )
    )

    with patch(
        "app.modules.visual_search.index_handler.visual_index_job_enabled",
        return_value=True,
    ), patch.object(handler, "_document") as document:
        result = handler._handle(context, {})

    assert result.error_code == "visual_index_schema_mismatch"
    assert result.outcome.value == "non_retryable_failure"
    document.assert_not_called()
