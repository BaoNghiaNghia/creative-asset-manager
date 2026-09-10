from __future__ import annotations
from base64 import b64encode
from io import BytesIO

import httpx
from PIL import Image

from app.modules.visual_search.contracts import EmbeddingDescriptor, VisualEmbedding, VisualEncoderUnavailableError
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR

class HttpVisualEncoder:
    """Synchronous client for the localhost-only isolated SigLIP process."""

    descriptor = VISUAL_SEARCH_BASELINE_DESCRIPTOR

    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        base_url = base_url.rstrip("/")
        self._url = base_url + "/v1/encode-image"
        self._text_url = base_url + "/v1/encode-text"
        self._timeout = timeout_seconds

    def encode_image(self, image: Image.Image) -> VisualEmbedding:
        stream = BytesIO()
        image.convert("RGB").save(stream, format="JPEG", quality=95, optimize=True)
        try:
            response = httpx.post(self._url, json={"image_base64": b64encode(stream.getvalue()).decode("ascii")}, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
            descriptor = EmbeddingDescriptor(**payload["descriptor"])
            values = tuple(float(value) for value in payload["values"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise VisualEncoderUnavailableError("isolated visual encoder is unavailable") from exc
        embedding = VisualEmbedding(descriptor, values)
        if embedding.descriptor != self.descriptor:
            raise VisualEncoderUnavailableError("isolated visual encoder descriptor mismatch")
        return embedding

    def encode_text(self, text: str) -> VisualEmbedding:
        try:
            response = httpx.post(self._text_url, json={"text": text}, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
            descriptor = EmbeddingDescriptor(**payload["descriptor"])
            values = tuple(float(value) for value in payload["values"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise VisualEncoderUnavailableError("isolated visual encoder is unavailable") from exc
        embedding = VisualEmbedding(descriptor, values)
        if embedding.descriptor != self.descriptor:
            raise VisualEncoderUnavailableError("isolated visual encoder descriptor mismatch")
        return embedding

class HttpVisualEncoderClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._encoder = HttpVisualEncoder(base_url, timeout_seconds)
    def get_encoder(self) -> HttpVisualEncoder:
        return self._encoder
