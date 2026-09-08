from __future__ import annotations

from typing import Protocol

from app.modules.visual_search.contracts import VisualEncoder


class IsolatedVisualEncoderClient(Protocol):
    """Worker-facing boundary for the future local/remote isolated encoder."""

    def get_encoder(self) -> VisualEncoder: ...
