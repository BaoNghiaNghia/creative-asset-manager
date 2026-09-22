from __future__ import annotations

from base64 import b64encode
from io import BytesIO
from typing import Literal

import httpx
from PIL import Image

from app.modules.visual_search.contracts import (
    EmbeddingDescriptor,
    VisualEmbedding,
    VisualEncoderQueueFullError,
    VisualEncoderQueueTimeoutError,
    VisualEncoderUnavailableError,
)
from app.modules.visual_search.model_spec import VISUAL_SEARCH_ACTIVE_DESCRIPTOR


EncoderPriority = Literal["interactive", "background"]


class HttpVisualEncoder:
    """Synchronous client for the localhost-only isolated SigLIP2 process."""

    descriptor = VISUAL_SEARCH_ACTIVE_DESCRIPTOR

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        internal_key: str = "",
    ) -> None:
        if not internal_key.strip():
            raise ValueError("visual encoder internal authentication is not configured")
        base_url = base_url.rstrip("/")
        self._url = base_url + "/v1/encode-image"
        self._text_url = base_url + "/v1/encode-text"
        self._timeout = timeout_seconds
        self._headers = {"Authorization": f"Bearer {internal_key}"}

    @staticmethod
    def _service_error(response: httpx.Response) -> VisualEncoderUnavailableError:
        code = ""
        try:
            detail = response.json().get("detail")
            if isinstance(detail, dict):
                code = str(detail.get("code") or "")
        except (TypeError, ValueError):
            pass
        if code == VisualEncoderQueueFullError.code:
            return VisualEncoderQueueFullError("isolated visual encoder queue is full")
        if code == VisualEncoderQueueTimeoutError.code:
            return VisualEncoderQueueTimeoutError(
                "isolated visual encoder queue wait timed out"
            )
        return VisualEncoderUnavailableError("isolated visual encoder is unavailable")

    def _request_embedding(
        self,
        url: str,
        payload: dict[str, str],
        *,
        priority: EncoderPriority,
    ) -> VisualEmbedding:
        try:
            response = httpx.post(
                url,
                json={**payload, "priority": priority},
                headers=self._headers,
                timeout=self._timeout,
            )
            if response.status_code == 503:
                raise self._service_error(response)
            response.raise_for_status()
            body = response.json()
            descriptor = EmbeddingDescriptor(**body["descriptor"])
            values = tuple(float(value) for value in body["values"])
            return VisualEmbedding(descriptor, values)
        except VisualEncoderUnavailableError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise VisualEncoderUnavailableError(
                "isolated visual encoder is unavailable"
            ) from exc

    def encode_image(
        self,
        image: Image.Image,
        *,
        priority: EncoderPriority = "interactive",
    ) -> VisualEmbedding:
        stream = BytesIO()
        image.convert("RGB").save(
            stream,
            format="JPEG",
            quality=95,
            optimize=True,
        )
        embedding = self._request_embedding(
            self._url,
            {"image_base64": b64encode(stream.getvalue()).decode("ascii")},
            priority=priority,
        )
        if embedding.descriptor != self.descriptor:
            raise VisualEncoderUnavailableError(
                "isolated visual encoder descriptor mismatch"
            )
        return embedding

    def encode_text(
        self,
        text: str,
        *,
        priority: EncoderPriority = "interactive",
    ) -> VisualEmbedding:
        embedding = self._request_embedding(
            self._text_url,
            {"text": text},
            priority=priority,
        )
        if embedding.descriptor != self.descriptor:
            raise VisualEncoderUnavailableError(
                "isolated visual encoder descriptor mismatch"
            )
        return embedding


class HttpVisualEncoderClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        internal_key: str = "",
    ) -> None:
        self._encoder = HttpVisualEncoder(base_url, timeout_seconds, internal_key)

    def get_encoder(self) -> HttpVisualEncoder:
        return self._encoder
