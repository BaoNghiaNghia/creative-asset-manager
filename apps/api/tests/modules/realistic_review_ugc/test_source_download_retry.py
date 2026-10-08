"""Regression: Pinterest CDN download exhaustion must free RRUGC Scout slots."""
from types import SimpleNamespace

import httpx
import pytest

from app.domain.processing.handlers import JobOutcome
from app.modules.processing.repository import ProcessingRepository  # noqa: F401 — initialize processing imports
from app.infrastructure.downloader.secure_image import SecureDownloadError
from app.modules.realistic_review_ugc import handler as rrugc_handler


@pytest.mark.parametrize(
    ("attempt", "terminal"),
    [(1, False), (2, False), (3, True), (4, True)],
)
def test_candidate_download_retry_is_bounded(monkeypatch, attempt, terminal):
    errors = []
    events = []

    async def unavailable(self, context):
        raise SecureDownloadError("Pinterest image cannot be fetched")

    monkeypatch.setattr(rrugc_handler.RrugcCandidateAnalyzeJobHandler, "_execute", unavailable)
    monkeypatch.setattr(
        rrugc_handler.RrugcCandidateAnalyzeJobHandler,
        "_mark_error",
        staticmethod(lambda context, code, *, terminal: errors.append((code, terminal))),
    )
    context = SimpleNamespace(
        job=SimpleNamespace(attempt_count=attempt, entity_id="candidate-1", tenant_id="tenant-a"),
        logger=SimpleNamespace(info=lambda name, extra: events.append((name, extra))),
    )
    result = rrugc_handler.RrugcCandidateAnalyzeJobHandler()(context)
    assert result.error_code == "rrugc_analysis_source_unavailable"
    assert result.outcome == (JobOutcome.NON_RETRYABLE_FAILURE if terminal else JobOutcome.RETRYABLE_FAILURE)
    assert errors == [("rrugc_analysis_source_unavailable", terminal)]
    assert len(events) == int(terminal)
    if terminal:
        assert events[0][0] == "rrugc_candidate_source_download_exhausted"
        assert "url" not in events[0][1]


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_permanent_cdn_http_error_finishes_without_retries(status):
    request = httpx.Request("GET", "https://i.pinimg.com/pin.jpg")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("unavailable", request=request, response=response)
    assert rrugc_handler.reference_source_download_terminal(1, error) is True


def test_rate_limited_cdn_keeps_bounded_retry():
    request = httpx.Request("GET", "https://i.pinimg.com/pin.jpg")
    response = httpx.Response(429, request=request)
    error = httpx.HTTPStatusError("rate-limited", request=request, response=response)
    assert rrugc_handler.reference_source_download_terminal(1, error) is False
    assert rrugc_handler.reference_source_download_terminal(3, error) is True
