from __future__ import annotations

import pytest

from app.modules.visual_search.fingerprint import (
    ExactContentFingerprint,
    sha256_fingerprint,
)


def test_exact_fingerprint_is_deterministic_and_content_sensitive() -> None:
    first = sha256_fingerprint(b"image-a")
    assert first == sha256_fingerprint(b"image-a")
    assert first != sha256_fingerprint(b"image-b")
    assert len(first.sha256) == 64


def test_exact_fingerprint_rejects_non_sha256_values() -> None:
    with pytest.raises(ValueError):
        ExactContentFingerprint("not-a-hash")
