from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.domain.processing.handlers import JobHandlerResult
from app.modules.visual_search.index_handler import VisualIndexSyncJobHandler
from app.modules.visual_search.metrics import VisualSearchMetrics


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
