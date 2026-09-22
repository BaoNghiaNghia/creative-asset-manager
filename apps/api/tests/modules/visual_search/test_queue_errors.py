from __future__ import annotations

from app.modules.visual_search.contracts import (
    VisualEncoderQueueFullError,
    VisualEncoderQueueTimeoutError,
)
from app.modules.visual_search.router import _encoder_http_error


def test_queue_full_maps_to_distinct_retryable_api_error() -> None:
    error = _encoder_http_error(VisualEncoderQueueFullError("full"))
    assert error.status_code == 503
    assert error.detail["code"] == "visual_encoder_queue_full"
    assert error.detail["retryable"] is True
    assert error.headers == {"Retry-After": "1"}


def test_queue_timeout_maps_to_distinct_retryable_api_error() -> None:
    error = _encoder_http_error(VisualEncoderQueueTimeoutError("timeout"))
    assert error.status_code == 503
    assert error.detail["code"] == "visual_encoder_queue_timeout"
    assert error.detail["retryable"] is True
    assert error.headers == {"Retry-After": "1"}
