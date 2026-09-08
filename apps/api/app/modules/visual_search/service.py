from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.config import Settings
from app.modules.visual_search.contracts import (
    EmbeddingDescriptor,
    VisualEncoder,
    VisualEncoderUnavailableError,
)


class VisualSearchError(RuntimeError):
    code = "visual_search_error"
    status_code = 503


class VisualSearchDisabledError(VisualSearchError):
    code = "visual_search_disabled"


class VisualSearchOperationDisabledError(VisualSearchDisabledError):
    code = "visual_search_operation_disabled"


class VisualSearchAssetUnavailableError(VisualSearchError):
    code = "visual_search_asset_unavailable"
    status_code = 409


class VisualEncoderProvider(Protocol):
    """Deferred provider boundary; it must not load a model at application boot."""

    def get_encoder(self) -> VisualEncoder: ...


class UnavailableVisualEncoderProvider:
    """Safe default until VS-02 supplies an isolated encoder client."""

    def get_encoder(self) -> VisualEncoder:
        raise VisualEncoderUnavailableError("No visual encoder has been configured.")


@dataclass(frozen=True, slots=True)
class VisualSearchCapabilities:
    enabled: bool
    upload_enabled: bool
    crop_enabled: bool
    hybrid_text_enabled: bool
    backfill_enabled: bool


class VisualSearchService:
    """Foundation-only policy boundary for later visual-search operations."""

    def __init__(
        self,
        settings: Settings,
        encoder_provider: VisualEncoderProvider | None = None,
    ) -> None:
        self._settings = settings
        self._encoder_provider = encoder_provider or UnavailableVisualEncoderProvider()

    def capabilities(self) -> VisualSearchCapabilities:
        enabled = self._settings.VISUAL_SEARCH_ENABLED
        return VisualSearchCapabilities(
            enabled=enabled,
            upload_enabled=enabled and self._settings.VISUAL_SEARCH_UPLOAD_ENABLED,
            crop_enabled=enabled and self._settings.VISUAL_SEARCH_CROP_ENABLED,
            hybrid_text_enabled=(
                enabled and self._settings.VISUAL_SEARCH_HYBRID_TEXT_ENABLED
            ),
            backfill_enabled=enabled and self._settings.VISUAL_SEARCH_BACKFILL_ENABLED,
        )

    def require_operation(
        self,
        operation: Literal["asset", "upload", "crop", "hybrid_text", "backfill"],
    ) -> None:
        capabilities = self.capabilities()
        if not capabilities.enabled:
            raise VisualSearchDisabledError("Visual search is disabled.")
        if operation == "upload" and not capabilities.upload_enabled:
            raise VisualSearchOperationDisabledError("Visual-search uploads are disabled.")
        if operation == "crop" and not capabilities.crop_enabled:
            raise VisualSearchOperationDisabledError("Visual-search crops are disabled.")
        if operation == "hybrid_text" and not capabilities.hybrid_text_enabled:
            raise VisualSearchOperationDisabledError("Visual-search text refinement is disabled.")
        if operation == "backfill" and not capabilities.backfill_enabled:
            raise VisualSearchOperationDisabledError("Visual-search backfill is disabled.")

    def encoder_descriptor(self) -> EmbeddingDescriptor:
        """Resolve only on an explicit enabled operation, never at app boot."""
        self.require_operation("asset")
        return self._encoder_provider.get_encoder().descriptor
