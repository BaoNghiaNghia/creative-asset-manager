from __future__ import annotations
from base64 import b64encode
from io import BytesIO
from time import sleep

import httpx
from PIL import Image

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding, VisualEncoderUnavailableError
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR

# The isolated encoder serializes inference. A foreground visual-search request can
# race a background indexing request, so retry only its explicit transient response.
_ENCODER_BUSY_RETRY_DELAYS_SECONDS = (0.2, 0.4, 0.8, 1.2)


class HttpVisualEncoder:
    """Synchronous client for the localhost-only isolated SigLIP process."""

    descriptor = VISUAL_SEARCH_BASELINE_DESCRIPTOR

    def __init__(self, base_url: str, timeout_seconds: float = 30.0, internal_key: str = "") -> None:
        if not internal_key.strip():
            raise ValueError("visual encoder internal authentication is not configured")
        base_url = base_url.rstrip("/")
        self._url = base_url + "/v1/encode-image"
        self._text_url = base_url + "/v1/encode-text"
        self._timeout = timeout_seconds
        self._headers = {"Authorization": f"Bearer {internal_key}"}

    def _request_embedding(self, url: str, payload: dict[str, str]) -> VisualEmbedding:
        try:
            for retry_delay in (*_ENCODER_BUSY_RETRY_DELAYS_SECONDS, None):
                response = httpx.post(url, json=payload, headers=self._headers, timeout=self._timeout)
                if response.status_code != 503:
                    response.raise_for_status()
                    body = response.json()
                    descriptor = EmbeddingDescriptor(**body["descriptor"])
                    values = tuple(float(value) for value in body["values"])
                    return VisualEmbedding(descriptor, values)
                if retry_delay is None:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = max(0.0, min(float(retry_after), 2.0)) if retry_after is not None else retry_delay
                except ValueError:
                    delay = retry_delay
                sleep(delay)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise VisualEncoderUnavailableError("isolated visual encoder is unavailable") from exc
        raise VisualEncoderUnavailableError("isolated visual encoder is unavailable")

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        stream = BytesIO()
        image.convert("RGB").save(stream, format="JPEG", quality=95, optimize=True)
        embedding = self._request_embedding(
            self._url,
            {"image_base64": b64encode(stream.getvalue()).decode("ascii")},
        )
        if embedding.descriptor != self.descriptor:
            raise VisualEncoderUnavailableError("isolated visual encoder descriptor mismatch")
        return embedding

    def encode_text(self, text: str) -> VisualEmbedding:
        embedding = self._request_embedding(self._text_url, {"text": text})
        if embedding.descriptor != self.descriptor:
            raise VisualEncoderUnavailableError("isolated visual encoder descriptor mismatch")
        return embedding


class HttpVisualEncoderClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0, internal_key: str = "") -> None:
        self._encoder = HttpVisualEncoder(base_url, timeout_seconds, internal_key)

    def get_encoder(self) -> HttpVisualEncoder:
        return self._encoder
