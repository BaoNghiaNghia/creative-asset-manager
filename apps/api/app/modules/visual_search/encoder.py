from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from app.modules.visual_search.contracts import (
    EmbeddingDescriptor,
    VisualEmbedding,
    VisualEncoder,
)

SIGLIP_BASELINE_MODEL = "google/siglip-base-patch16-224"
SIGLIP_BASELINE_REVISION = "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed"
SIGLIP_BASELINE_DIMENSION = 768
SIGLIP_BASELINE_PREPROCESS_VERSION = "siglip-224-transformers-4.46.3-v1"


class EncoderContractViolationError(RuntimeError):
    """An isolated encoder returned data incompatible with its descriptor."""


class SiglipEncoderLoadError(RuntimeError):
    """The isolated encoder runtime or pinned local snapshot is unavailable."""


class ValidatedVisualEncoder:
    """Validate a pluggable encoder without importing or loading an ML runtime."""

    def __init__(self, delegate: VisualEncoder):
        self._delegate = delegate
        self._descriptor = delegate.descriptor

    @property
    def descriptor(self) -> EmbeddingDescriptor:
        return self._descriptor

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        embedding = self._delegate.encode_image(image)
        if embedding.descriptor != self._descriptor:
            raise EncoderContractViolationError(
                "encoder returned an embedding with a different descriptor"
            )
        return embedding


class SiglipVisualEncoder:
    """Pinned SigLIP encoder for the future isolated local encoder process.

    This module deliberately imports no ML runtime at module import time. The
    constructor may only run in the dedicated encoder environment, never in a
    FastAPI request process or the shared CAM worker environment.
    """

    descriptor = EmbeddingDescriptor(
        encoder_name=SIGLIP_BASELINE_MODEL,
        encoder_revision=SIGLIP_BASELINE_REVISION,
        embedding_schema_version="visual_embedding_v1",
        dimension=SIGLIP_BASELINE_DIMENSION,
        preprocess_version=SIGLIP_BASELINE_PREPROCESS_VERSION,
    )

    def __init__(self, model_path: str | Path):
        resolved_path = Path(model_path)
        if (
            not resolved_path.is_dir()
            or resolved_path.name != SIGLIP_BASELINE_REVISION
        ):
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
            self._model = AutoModel.from_pretrained(
                resolved_path,
                local_files_only=True,
            ).eval()
            self._processor = AutoProcessor.from_pretrained(
                resolved_path,
                local_files_only=True,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise SiglipEncoderLoadError(
                "Pinned SigLIP snapshot could not be loaded."
            ) from exc

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        with self._torch.inference_mode():
            inputs = self._processor(
                images=image.convert("RGB"),
                return_tensors="pt",
            )
            vector = self._model.get_image_features(**inputs)
            vector = self._torch.nn.functional.normalize(vector, dim=-1)
            values = tuple(float(value) for value in vector.squeeze(0).tolist())
        return VisualEmbedding(self.descriptor, values)
