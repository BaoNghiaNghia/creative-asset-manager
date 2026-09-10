"""Heavy SigLIP runtime owned exclusively by the isolated visual-encoder service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.model_spec import (
    SIGLIP_BASELINE_REVISION,
    VISUAL_SEARCH_BASELINE_DESCRIPTOR,
)


class SiglipEncoderLoadError(RuntimeError):
    """The isolated encoder runtime or pinned local snapshot is unavailable."""


class SiglipVisualEncoder:
    """Pinned SigLIP implementation for the dedicated encoder runtime only."""

    descriptor = VISUAL_SEARCH_BASELINE_DESCRIPTOR

    def __init__(self, model_path: str | Path):
        resolved_path = Path(model_path)
        if not resolved_path.is_dir() or resolved_path.name != SIGLIP_BASELINE_REVISION:
            raise SiglipEncoderLoadError("Pinned SigLIP snapshot is unavailable.")

        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise SiglipEncoderLoadError(
                "SigLIP must run in the isolated visual-encoder environment."
            ) from exc

        try:
            self._torch: Any = torch
            self._model = AutoModel.from_pretrained(resolved_path, local_files_only=True).eval()
            self._processor = AutoProcessor.from_pretrained(resolved_path, local_files_only=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise SiglipEncoderLoadError("Pinned SigLIP snapshot could not be loaded.") from exc

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        with self._torch.inference_mode():
            inputs = self._processor(images=image.convert("RGB"), return_tensors="pt")
            vector = self._torch.nn.functional.normalize(self._model.get_image_features(**inputs), dim=-1)
            values = tuple(float(value) for value in vector.squeeze(0).tolist())
        return VisualEmbedding(self.descriptor, values)

    def encode_text(self, text: str) -> VisualEmbedding:
        value = text.strip()
        if not value:
            raise ValueError("text must be non-empty")
        with self._torch.inference_mode():
            inputs = self._processor(text=[value], padding="max_length", truncation=True, return_tensors="pt")
            vector = self._torch.nn.functional.normalize(self._model.get_text_features(**inputs), dim=-1)
            values = tuple(float(item) for item in vector.squeeze(0).tolist())
        return VisualEmbedding(self.descriptor, values)
