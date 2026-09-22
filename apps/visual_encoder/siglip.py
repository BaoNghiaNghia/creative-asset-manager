"""SigLIP2 runtimes owned exclusively by the isolated visual-encoder service."""

from __future__ import annotations

import hashlib
import json
from math import sqrt
import threading
from pathlib import Path
from typing import Any

from PIL import Image

from app.modules.visual_search.contracts import VisualEmbedding
from app.modules.visual_search.model_spec import (
    SIGLIP2_REVISION,
    VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
)


OPENVINO_ARTIFACT_FORMAT = "cam-siglip2-openvino-v1"
OPENVINO_BASELINE_VERSION = "2026.4.0"
OPENVINO_ARTIFACT_FILES = (
    "image_encoder.xml",
    "image_encoder.bin",
    "text_encoder.xml",
    "text_encoder.bin",
)


class SiglipEncoderLoadError(RuntimeError):
    """The isolated encoder runtime or pinned local snapshot is unavailable."""


def _validate_model_path(model_path: str | Path) -> Path:
    resolved_path = Path(model_path)
    if not resolved_path.is_dir() or resolved_path.name != SIGLIP2_REVISION:
        raise SiglipEncoderLoadError("Pinned SigLIP2 snapshot is unavailable.")
    return resolved_path


def _validate_processor_contract(processor: Any) -> None:
    image_size = getattr(getattr(processor, "image_processor", None), "size", {})
    runtime_height = int(image_size.get("height", image_size.get("shortest_edge", 0)))
    runtime_width = int(image_size.get("width", image_size.get("shortest_edge", 0)))
    if (runtime_height, runtime_width) != (224, 224):
        raise ValueError("SigLIP2 image preprocess size does not match descriptor")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _descriptor_manifest() -> dict[str, object]:
    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR
    return {
        "encoder_name": descriptor.encoder_name,
        "encoder_revision": descriptor.encoder_revision,
        "embedding_schema_version": descriptor.embedding_schema_version,
        "dimension": descriptor.dimension,
        "preprocess_version": descriptor.preprocess_version,
        "similarity": descriptor.similarity,
    }


def _normalized_embedding(values: Any) -> VisualEmbedding:
    data = values.tolist() if hasattr(values, "tolist") else values
    if isinstance(data, (list, tuple)) and len(data) == 1 and isinstance(data[0], (list, tuple)):
        data = data[0]
    vector = tuple(float(value) for value in data)
    if len(vector) != VISUAL_SEARCH_ACTIVE_DESCRIPTOR.dimension:
        raise SiglipEncoderLoadError("SigLIP2 runtime returned an incompatible vector dimension.")
    norm = sqrt(sum(value * value for value in vector))
    if not norm:
        raise SiglipEncoderLoadError("SigLIP2 runtime returned a zero vector.")
    return VisualEmbedding(
        VISUAL_SEARCH_ACTIVE_DESCRIPTOR,
        tuple(value / norm for value in vector),
    )


class SiglipVisualEncoder:
    """Pinned Transformers/PyTorch fallback for the dedicated encoder runtime."""

    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR

    def __init__(
        self,
        model_path: str | Path,
        *,
        inference_threads: int = 2,
        interop_threads: int = 1,
    ):
        if inference_threads <= 0:
            raise SiglipEncoderLoadError(
                "Transformers inference threads must be positive."
            )
        if interop_threads <= 0:
            raise SiglipEncoderLoadError(
                "Transformers interop threads must be positive."
            )
        resolved_path = _validate_model_path(model_path)

        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise SiglipEncoderLoadError(
                "SigLIP2 must run in the isolated visual-encoder environment."
            ) from exc

        try:
            self._torch: Any = torch
            torch.set_num_threads(inference_threads)
            torch.set_num_interop_threads(interop_threads)
            self._inference_thread_state = threading.local()
            self._model = AutoModel.from_pretrained(
                resolved_path,
                local_files_only=True,
            ).eval()
            self._processor = AutoProcessor.from_pretrained(
                resolved_path,
                local_files_only=True,
            )
            text_config = self._model.config.text_config
            runtime_dimension = int(
                getattr(text_config, "projection_size", getattr(text_config, "hidden_size", 0))
            )
            _validate_processor_contract(self._processor)
            if runtime_dimension != self.descriptor.dimension:
                raise ValueError("SigLIP2 projection dimension does not match descriptor")
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            raise SiglipEncoderLoadError(
                "Pinned SigLIP2 snapshot could not be loaded."
            ) from exc
        self._inference_threads = inference_threads
        self._interop_threads = interop_threads

    def runtime_info(self) -> dict[str, object]:
        return {
            "runtime": "transformers",
            "transformers_inference_threads": self._inference_threads,
            "transformers_interop_threads": self._interop_threads,
        }

    def _prepare_inference_thread(self) -> None:
        if getattr(self._inference_thread_state, "initialized", False):
            return
        init_num_threads = getattr(self._torch, "init_num_threads", None)
        if callable(init_num_threads):
            init_num_threads()
        self._inference_thread_state.initialized = True

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        self._prepare_inference_thread()
        with self._torch.inference_mode():
            inputs = self._processor(images=image.convert("RGB"), return_tensors="pt")
            vector = self._torch.nn.functional.normalize(
                self._model.get_image_features(**inputs),
                dim=-1,
            )
            values = tuple(float(value) for value in vector.squeeze(0).tolist())
        return VisualEmbedding(self.descriptor, values)

    def encode_text(self, text: str) -> VisualEmbedding:
        self._prepare_inference_thread()
        value = text.strip()
        if not value:
            raise ValueError("text must be non-empty")
        with self._torch.inference_mode():
            inputs = self._processor(
                text=[value],
                padding="max_length",
                truncation=True,
                return_tensors="pt",
            )
            vector = self._torch.nn.functional.normalize(
                self._model.get_text_features(**inputs),
                dim=-1,
            )
            values = tuple(float(item) for item in vector.squeeze(0).tolist())
        return VisualEmbedding(self.descriptor, values)


class OpenVinoSiglip2Encoder:
    """FP32 OpenVINO baseline for the two non-overlapping SigLIP2 towers."""

    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR

    def __init__(
        self,
        model_path: str | Path,
        artifact_path: str | Path,
        *,
        inference_threads: int = 2,
        streams: int = 1,
    ) -> None:
        if inference_threads <= 0:
            raise SiglipEncoderLoadError("OpenVINO inference threads must be positive.")
        if streams <= 0:
            raise SiglipEncoderLoadError("OpenVINO streams must be positive.")

        resolved_model_path = _validate_model_path(model_path)
        resolved_artifact_path = Path(artifact_path)
        manifest_path = resolved_artifact_path / "manifest.json"
        image_model_path = resolved_artifact_path / "image_encoder.xml"
        text_model_path = resolved_artifact_path / "text_encoder.xml"
        artifact_files = {
            name: resolved_artifact_path / name
            for name in OPENVINO_ARTIFACT_FILES
        }
        if not (
            resolved_artifact_path.is_dir()
            and manifest_path.is_file()
            and all(path.is_file() for path in artifact_files.values())
        ):
            raise SiglipEncoderLoadError("Pinned SigLIP2 OpenVINO artifact is unavailable.")

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SiglipEncoderLoadError("SigLIP2 OpenVINO manifest is invalid.") from exc

        expected = {
            "artifact_format": OPENVINO_ARTIFACT_FORMAT,
            "precision": "fp32",
            "openvino_version": OPENVINO_BASELINE_VERSION,
            **_descriptor_manifest(),
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise SiglipEncoderLoadError(
                "SigLIP2 OpenVINO artifact does not match the active embedding contract."
            )
        recorded_files = manifest.get("files")
        if not isinstance(recorded_files, dict):
            raise SiglipEncoderLoadError("SigLIP2 OpenVINO artifact hashes are missing.")
        for name, path in artifact_files.items():
            if recorded_files.get(name) != _sha256_file(path):
                raise SiglipEncoderLoadError("SigLIP2 OpenVINO artifact hash mismatch.")

        try:
            import openvino as ov
            import transformers
            from transformers import AutoProcessor

            if not str(getattr(ov, "__version__", "")).startswith(OPENVINO_BASELINE_VERSION):
                raise ValueError("OpenVINO runtime version does not match exported artifact")
            if getattr(transformers, "__version__", "") != "4.51.3":
                raise ValueError("Transformers runtime version does not match preprocess contract")

            self._processor = AutoProcessor.from_pretrained(
                resolved_model_path,
                local_files_only=True,
            )
            _validate_processor_contract(self._processor)
            self._core = ov.Core()
            compile_config = {
                "INFERENCE_NUM_THREADS": inference_threads,
                "NUM_STREAMS": streams,
            }
            image_model = self._core.read_model(str(image_model_path))
            text_model = self._core.read_model(str(text_model_path))
            self._image_compiled = self._core.compile_model(
                image_model,
                "CPU",
                compile_config,
            )
            self._text_compiled = self._core.compile_model(
                text_model,
                "CPU",
                compile_config,
            )
            self._image_request = self._image_compiled.create_infer_request()
            self._text_request = self._text_compiled.create_infer_request()
        except (ImportError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise SiglipEncoderLoadError(
                "Pinned SigLIP2 OpenVINO artifact could not be loaded."
            ) from exc

        self._inference_threads = inference_threads
        self._streams = streams

    def runtime_info(self) -> dict[str, object]:
        return {
            "runtime": "openvino",
            "openvino_inference_threads": self._inference_threads,
            "openvino_streams": self._streams,
        }

    @staticmethod
    def _first_output(result: Any) -> Any:
        try:
            return next(iter(result.values()))
        except (AttributeError, StopIteration) as exc:
            raise SiglipEncoderLoadError(
                "SigLIP2 OpenVINO runtime returned no embedding output."
            ) from exc

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        inputs = self._processor(
            images=image.convert("RGB"),
            return_tensors="np",
        )
        try:
            result = self._image_request.infer(
                {"pixel_values": inputs["pixel_values"]}
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise SiglipEncoderLoadError("SigLIP2 OpenVINO image inference failed.") from exc
        return _normalized_embedding(self._first_output(result))

    def encode_text(self, text: str) -> VisualEmbedding:
        value = text.strip()
        if not value:
            raise ValueError("text must be non-empty")
        inputs = self._processor(
            text=[value],
            padding="max_length",
            truncation=True,
            return_tensors="np",
        )
        try:
            result = self._text_request.infer(
                {
                    "input_ids": inputs["input_ids"],
                    "attention_mask": inputs["attention_mask"],
                }
            )
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise SiglipEncoderLoadError("SigLIP2 OpenVINO text inference failed.") from exc
        return _normalized_embedding(self._first_output(result))


def build_visual_encoder(
    *,
    runtime: str,
    model_path: str | Path,
    openvino_artifact_path: str | Path = "",
    inference_threads: int = 2,
    streams: int = 1,
    transformers_inference_threads: int = 2,
    transformers_interop_threads: int = 1,
) -> SiglipVisualEncoder | OpenVinoSiglip2Encoder:
    normalized_runtime = runtime.strip().casefold()
    if normalized_runtime in {"transformers", "pytorch"}:
        return SiglipVisualEncoder(
            model_path,
            inference_threads=transformers_inference_threads,
            interop_threads=transformers_interop_threads,
        )
    if normalized_runtime == "openvino":
        return OpenVinoSiglip2Encoder(
            model_path,
            openvino_artifact_path,
            inference_threads=inference_threads,
            streams=streams,
        )
    raise SiglipEncoderLoadError(
        "VISUAL_ENCODER_RUNTIME must be 'transformers' or 'openvino'."
    )
