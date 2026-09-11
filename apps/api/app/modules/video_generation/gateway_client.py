from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ALLOWED_STATES = {
    "accepted", "submitted", "running", "submission_unknown", "completed", "failed",
}
_ALLOWED_CONTENT_TYPES = {"video/mp4"}


class DolaGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GatewayReference:
    filename: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class GatewayGeneration:
    generation_id: str
    status: str
    idempotent_replay: bool = False
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayContent:
    content_type: str
    content_length: int | None
    body: object


def validate_gateway_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "http" or host not in _ALLOWED_HOSTS or parsed.username or parsed.password:
        raise DolaGatewayError(
            "dola_gateway_url_unsafe",
            "Dola gateway URL must target a loopback HTTP endpoint.",
        )
    return raw_url.rstrip("/")


class DolaGatewayClient:
    """CAM-owned HTTP boundary for the localhost Dola render gateway."""

    def __init__(self, settings, *, client: httpx.AsyncClient | None = None):
        self.base_url = validate_gateway_url(settings.DOLA_RENDER_GATEWAY_URL)
        self.internal_key = settings.DOLA_RENDER_GATEWAY_INTERNAL_KEY.strip()
        self.timeout_seconds = max(1, int(settings.DOLA_RENDER_GATEWAY_TIMEOUT_SECONDS))
        self.content_timeout_seconds = max(
            self.timeout_seconds, int(settings.DOLA_RENDER_GATEWAY_CONTENT_TIMEOUT_SECONDS)
        )
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, read=self.content_timeout_seconds)
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        if not self.internal_key:
            raise DolaGatewayError(
                "dola_gateway_not_configured",
                "Dola gateway authentication is unavailable.",
            )
        headers = {"Authorization": f"Bearer {self.internal_key}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def submit(self, *, run_id: str, prompt: str, model: str, aspect_ratio: str,
                     duration_seconds: int, references: list[GatewayReference]) -> GatewayGeneration:
        metadata = json.dumps({
            "prompt": prompt, "model": model, "aspect_ratio": aspect_ratio,
            "duration_seconds": duration_seconds,
        }, separators=(",", ":"))
        files: list[tuple[str, tuple[str | None, bytes | str, str | None]]] = [
            ("metadata", (None, metadata, None))
        ]
        files.extend(
            ("references", (reference.filename, reference.content, reference.mime_type))
            for reference in references
        )
        response = await self._request(
            "POST", "/internal/v1/video-generations",
            headers=self._headers(idempotency_key=f"video-generate:{run_id}"), files=files,
        )
        return self._parse(response)

    async def get_generation(self, generation_id: str) -> GatewayGeneration:
        response = await self._request(
            "GET", f"/internal/v1/video-generations/{generation_id}",
            headers=self._headers(),
        )
        return self._parse(response)

    @asynccontextmanager
    async def open_content(self, generation_id: str, *, maximum_bytes: int):
        """Yield authenticated gateway content without buffering it in memory."""
        try:
            async with self._client.stream(
                "GET", self.base_url + f"/internal/v1/video-generations/{generation_id}/content",
                headers=self._headers(),
            ) as response:
                self._raise_for_response(response)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in _ALLOWED_CONTENT_TYPES:
                    raise DolaGatewayError("dola_generation_content_invalid", "Dola gateway returned invalid generated video content.")
                try:
                    content_length = int(response.headers["content-length"]) if "content-length" in response.headers else None
                except ValueError:
                    content_length = None
                if content_length is not None and (content_length <= 0 or content_length > maximum_bytes):
                    raise DolaGatewayError("video_generation_output_too_large", "Generated video exceeds the configured output limit.")
                yield GatewayContent(content_type, content_length, response.aiter_bytes())
        except DolaGatewayError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise DolaGatewayError("dola_gateway_transport_error", "Dola gateway is temporarily unavailable.", retryable=True) from exc

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._client.request(method, self.base_url + path, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise DolaGatewayError(
                "dola_gateway_transport_error", "Dola gateway is temporarily unavailable.", retryable=True
            ) from exc
        self._raise_for_response(response)
        return response

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if response.status_code in {502, 503, 504}:
            raise DolaGatewayError(
                "dola_gateway_unavailable", "Dola gateway is temporarily unavailable.", retryable=True
            )
        if response.status_code == 409:
            raise DolaGatewayError(
                "dola_idempotency_key_conflict", "Dola gateway rejected the idempotency key."
            )
        if response.status_code == 404:
            raise DolaGatewayError(
                "dola_generation_not_found", "Dola gateway generation was not found."
            )
        if response.status_code in {401, 403}:
            raise DolaGatewayError(
                "dola_gateway_auth_failed", "Dola gateway authentication failed."
            )
        if response.is_error:
            raise DolaGatewayError(
                "dola_gateway_request_failed", "Dola gateway rejected the request."
            )

    @staticmethod
    def _parse(response: httpx.Response) -> GatewayGeneration:
        try:
            payload = response.json()
        except ValueError as exc:
            raise DolaGatewayError(
                "dola_gateway_invalid_response", "Dola gateway returned an invalid response."
            ) from exc
        generation_id = payload.get("generation_id") if isinstance(payload, dict) else None
        status = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(generation_id, str) or not generation_id or not isinstance(status, str) or status not in _ALLOWED_STATES:
            raise DolaGatewayError(
                "dola_gateway_invalid_response", "Dola gateway returned an invalid response."
            )
        return GatewayGeneration(
            generation_id=generation_id,
            status=status,
            idempotent_replay=bool(payload.get("idempotent_replay")),
            error_code=payload.get("error_code") if isinstance(payload.get("error_code"), str) else None,
            error_message=payload.get("error_message") if isinstance(payload.get("error_message"), str) else None,
        )
