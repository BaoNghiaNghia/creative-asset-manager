from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExactContentFingerprint:
    """SHA-256 exact-content identity; this is not a semantic similarity signal."""

    sha256: str

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.sha256
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")


def sha256_fingerprint(content: bytes) -> ExactContentFingerprint:
    return ExactContentFingerprint(hashlib.sha256(content).hexdigest())
