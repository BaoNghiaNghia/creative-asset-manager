"""Review Scout compatibility must not weaken general ingestion validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.realistic_review_ugc.schema import (
    AutoScoutCandidateBatchRequest,
    CandidateBatchRequest,
)


def test_old_scout_pin_only_fallback_sanitized_without_weakening_general_api():
    payload = {
        "items": [
            {"pin_url": "https://www.pinterest.com/pin/1/", "image_url": ""},
            {
                "pin_url": "https://www.pinterest.com/pin/2/",
                "image_url": "https://i.pinimg.com/736x/aa/bb/good.jpg",
                "alt_text": "a" * 2401,
            },
        ],
        "source_query": "u" * 600,
    }
    with pytest.raises(ValidationError):
        CandidateBatchRequest.model_validate(payload)
    clean = AutoScoutCandidateBatchRequest.model_validate(payload)
    assert len(clean.items) == 1
    assert clean.items[0].pin_url.endswith("/pin/2/")
    assert len(clean.items[0].alt_text or "") == 2000
    assert len(clean.source_query or "") == 500


def test_legacy_compatibility_does_not_accept_invalid_nonempty_urls():
    payload = {
        "items": [{
            "pin_url": "https://www.pinterest.com/pin/1/",
            "image_url": "https://unsafe.example/image.jpg",
        }],
    }
    # Syntax validation doesn't replace the existing service's URL allowlist.
    clean = AutoScoutCandidateBatchRequest.model_validate(payload)
    assert len(clean.items) == 1
    assert clean.items[0].image_url == payload["items"][0]["image_url"]


def test_scout_compatibility_preserves_batch_size_limit():
    payload = {"items": [
        {"pin_url": "https://www.pinterest.com/pin/1/", "image_url": ""}
        for _ in range(51)
    ]}
    with pytest.raises(ValidationError):
        AutoScoutCandidateBatchRequest.model_validate(payload)
