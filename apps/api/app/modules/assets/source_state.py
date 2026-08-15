from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.modules.assets.model import ExternalSourceModel


def is_external_source_decommissioned(
    source: ExternalSourceModel | Mapping[str, Any] | None,
) -> bool:
    """Return whether a source was explicitly retired while retaining history.

    Only a meaningful source_metadata.decommissioned_at marks a source as
    decommissioned. Default-source flags, canonical IDs, OAuth health, and
    asset counts are not lifecycle signals.
    """
    if source is None:
        return False
    metadata = (
        source.source_metadata
        if isinstance(source, ExternalSourceModel)
        else source
    )
    if not isinstance(metadata, Mapping):
        return False
    value = metadata.get("decommissioned_at")
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)
