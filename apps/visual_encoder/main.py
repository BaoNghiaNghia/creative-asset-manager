from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import warnings
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from embedding_cache import (
    BoundedEmbeddingCache,
    image_cache_key,
    text_cache_key,
)
from inference_queue import (
    BoundedInferenceQueue,
    InferenceQueueClosedError,
    InferenceQueueFullError,
    InferenceQueueTimeoutError,
)
from siglip import build_visual_encoder


_MAX_BYTES = 8_000_000
_MAX_BASE64 = 4 * ((_MAX_BYTES + 2) // 3) + 16
_MAX_WIDTH = _MAX_HEIGHT = 20_000
_MAX_PIXELS = 120_000_000

encoder: Any | None = None
inference_queue: BoundedInferenceQueue | None = None
image_cache: BoundedEmbeddingCache | None = None
text_cache: BoundedEmbeddingCache | None = None


class EncodeRequest(BaseModel):
    image_base64: str
    priority: Literal["interactive", "background"] = "interactive"


class EncodeTextRequest(BaseModel):
    text: str
    priority: Literal["interactive", "background"] = "interactive"


class EncodeResponse(BaseModel):
    descriptor: dict[str, object]
    values: list[float]


def _require(authorization: str | None) -> None:
    expected = os.environ.get("VISUAL_ENCODER_INTERNAL_KEY", "")
    token = (
        authorization[7:]
        if authorization and authorization.startswith("Bearer ")
        else ""
    )
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(401, "unauthorized")


def _decode_raw_request(raw: bytes) -> tuple[Image.Image, str]:
    if not raw:
        raise HTTPException(422, "invalid image payload")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, "image payload exceeds limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as probe:
                if probe.format != "JPEG":
                    raise HTTPException(422, "invalid image payload")
                width, height = probe.size
                if (
                    width > _MAX_WIDTH
                    or height > _MAX_HEIGHT
                    or width * height > _MAX_PIXELS
                ):
                    raise HTTPException(413, "image payload exceeds limit")
                probe.verify()
            with Image.open(BytesIO(raw)) as opened:
                opened.load()
                image = opened.convert("RGB").copy()
        return image, hashlib.sha256(raw).hexdigest()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "image payload exceeds limit")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(422, "invalid image payload") from exc


def _decode_request(value: str) -> tuple[Image.Image, str]:
    if not value:
        raise HTTPException(422, "invalid image payload")
    if len(value) > _MAX_BASE64:
        raise HTTPException(413, "image payload exceeds limit")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "invalid image payload") from exc
    return _decode_raw_request(raw)


def _decode(value: str) -> Image.Image:
    image, _request_sha256 = _decode_request(value)
    return image


def _response(result: Any) -> dict[str, object]:
    descriptor = result.descriptor
    return {
        "descriptor": {
            "encoder_name": descriptor.encoder_name,
            "encoder_revision": descriptor.encoder_revision,
            "embedding_schema_version": descriptor.embedding_schema_version,
            "dimension": descriptor.dimension,
            "preprocess_version": descriptor.preprocess_version,
            "similarity": descriptor.similarity,
        },
        "values": list(result.values),
    }


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _non_negative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return value


def _positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _build_encoder() -> Any:
    return build_visual_encoder(
        runtime=os.environ.get("VISUAL_ENCODER_RUNTIME", "transformers"),
        model_path=os.environ.get("VISUAL_ENCODER_MODEL_PATH", ""),
        openvino_artifact_path=os.environ.get(
            "VISUAL_ENCODER_OPENVINO_MODEL_PATH",
            "",
        ),
        inference_threads=_positive_int_env(
            "VISUAL_ENCODER_OPENVINO_INFERENCE_THREADS",
            2,
        ),
        streams=_positive_int_env(
            "VISUAL_ENCODER_OPENVINO_STREAMS",
            1,
        ),
        transformers_inference_threads=_positive_int_env(
            "VISUAL_ENCODER_TRANSFORMERS_INFERENCE_THREADS",
            2,
        ),
        transformers_interop_threads=_positive_int_env(
            "VISUAL_ENCODER_TRANSFORMERS_INTEROP_THREADS",
            1,
        ),
    )


def _build_inference_queue() -> BoundedInferenceQueue:
    return BoundedInferenceQueue(
        max_queue_size=_positive_int_env(
            "VISUAL_ENCODER_QUEUE_MAX_SIZE",
            8,
        ),
        interactive_reserve=_non_negative_int_env(
            "VISUAL_ENCODER_QUEUE_INTERACTIVE_RESERVE",
            2,
        ),
        wait_timeout_seconds=_positive_float_env(
            "VISUAL_ENCODER_QUEUE_WAIT_TIMEOUT_SECONDS",
            5.0,
        ),
    )


def _build_embedding_caches() -> tuple[BoundedEmbeddingCache, BoundedEmbeddingCache]:
    return (
        BoundedEmbeddingCache(
            _non_negative_int_env("VISUAL_ENCODER_IMAGE_CACHE_MAX_ENTRIES", 128)
        ),
        BoundedEmbeddingCache(
            _non_negative_int_env("VISUAL_ENCODER_TEXT_CACHE_MAX_ENTRIES", 256)
        ),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global encoder, inference_queue, image_cache, text_cache
    import asyncio

    encoder = await asyncio.to_thread(_build_encoder)
    inference_queue = _build_inference_queue()
    image_cache, text_cache = _build_embedding_caches()
    await inference_queue.start()
    try:
        yield
    finally:
        if inference_queue is not None:
            await inference_queue.close()
        if image_cache is not None:
            image_cache.clear()
        if text_cache is not None:
            text_cache.clear()
        inference_queue = None
        image_cache = None
        text_cache = None
        encoder = None


app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, object]:
    if (
        encoder is None
        or inference_queue is None
        or image_cache is None
        or text_cache is None
    ):
        raise HTTPException(503, "encoder unavailable")
    payload: dict[str, object] = {
        "status": "ok",
        "dimension": encoder.descriptor.dimension,
        "schema": encoder.descriptor.embedding_schema_version,
        "queue": inference_queue.snapshot(),
        "cache": {
            "image": image_cache.snapshot(),
            "text": text_cache.snapshot(),
        },
    }
    runtime_info = getattr(encoder, "runtime_info", None)
    if callable(runtime_info):
        payload.update(runtime_info())
    return payload


def _queue_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InferenceQueueFullError):
        code = "visual_encoder_queue_full"
        message = "Visual encoder queue is full."
    elif isinstance(exc, InferenceQueueTimeoutError):
        code = "visual_encoder_queue_timeout"
        message = "Visual encoder queue wait timed out."
    else:
        code = "visual_encoder_unavailable"
        message = "Visual encoder is unavailable."
    return HTTPException(
        503,
        detail={
            "code": code,
            "message": message,
            "retryable": True,
        },
        headers={"Retry-After": "1"},
    )


async def _submit(operation, *, priority: Literal["interactive", "background"]):
    if encoder is None or inference_queue is None:
        raise HTTPException(
            503,
            detail={
                "code": "visual_encoder_unavailable",
                "message": "Visual encoder is unavailable.",
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        )
    try:
        return await inference_queue.submit(operation, priority=priority)
    except (
        InferenceQueueFullError,
        InferenceQueueTimeoutError,
        InferenceQueueClosedError,
    ) as exc:
        raise _queue_error(exc) from exc


async def _encode_image_decoded(
    image: Image.Image,
    request_sha256: str,
    *,
    priority: Literal["interactive", "background"],
) -> dict[str, object]:
    if encoder is None or image_cache is None:
        raise HTTPException(503, "encoder unavailable")
    key = image_cache_key(request_sha256, encoder.descriptor)
    cached = image_cache.get(key)
    if cached is not None:
        return _response(cached)

    def encode_miss():
        assert encoder is not None
        assert image_cache is not None
        duplicate = image_cache.peek(key)
        if duplicate is not None:
            return duplicate
        result = encoder.encode_image(image)
        image_cache.put(key, result)
        return result

    result = await _submit(encode_miss, priority=priority)
    return _response(result)


async def _read_raw_image_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_BYTES:
            raise HTTPException(413, "image payload exceeds limit")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/v1/encode-image", response_model=EncodeResponse)
async def encode(
    body: EncodeRequest,
    authorization: str | None = Header(default=None),
):
    _require(authorization)
    image, request_sha256 = _decode_request(body.image_base64)
    return await _encode_image_decoded(
        image,
        request_sha256,
        priority=body.priority,
    )


@app.post("/v1/encode-image-bytes", response_model=EncodeResponse)
async def encode_image_bytes(
    request: Request,
    priority: Literal["interactive", "background"] = "interactive",
    authorization: str | None = Header(default=None),
):
    _require(authorization)
    raw = await _read_raw_image_body(request)
    image, request_sha256 = _decode_raw_request(raw)
    return await _encode_image_decoded(
        image,
        request_sha256,
        priority=priority,
    )


@app.post("/v1/encode-text", response_model=EncodeResponse)
async def encode_text(
    body: EncodeTextRequest,
    authorization: str | None = Header(default=None),
):
    _require(authorization)
    if encoder is None or text_cache is None:
        raise HTTPException(503, "encoder unavailable")
    text = body.text.strip()
    if not text or len(text) > 500:
        raise HTTPException(422, "text is required")
    key = text_cache_key(text, encoder.descriptor)
    cached = text_cache.get(key)
    if cached is not None:
        return _response(cached)

    def encode_miss():
        assert encoder is not None
        assert text_cache is not None
        duplicate = text_cache.peek(key)
        if duplicate is not None:
            return duplicate
        result = encoder.encode_text(text)
        text_cache.put(key, result)
        return result

    result = await _submit(encode_miss, priority=body.priority)
    return _response(result)
