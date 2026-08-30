from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.domain.providers.contracts import AiProviderError
from app.modules.video_search.proxy import PreparedVideoChunk


_LOGGER = logging.getLogger("cam.providers.gemini_video")
_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
_UPLOAD_ROOT = "https://generativelanguage.googleapis.com/upload/v1beta"
_UPLOAD_BLOCK_SIZE = 1024 * 1024
MEDIA_RESOLUTION_LOW = "MEDIA_RESOLUTION_LOW"
MEDIA_RESOLUTION_HIGH = "MEDIA_RESOLUTION_HIGH"
_MEDIA_RESOLUTIONS = frozenset((MEDIA_RESOLUTION_LOW, MEDIA_RESOLUTION_HIGH))
_MAX_ERROR_DETAIL_CHARS = 1200


@dataclass(frozen=True, slots=True)
class GeminiUploadedVideo:
    name: str
    uri: str
    mime_type: str
    state: str


@dataclass(frozen=True, slots=True)
class GeminiVideoGeneration:
    document: Mapping[str, Any]
    provider: str
    model: str | None
    provider_request_id: str | None
    usage: Mapping[str, Any]
    provider_metadata: Mapping[str, Any]


class GeminiVideoClient:
    """Narrow Gemini Files API adapter for already-prepared MP4 proxy chunks.

    The caller owns no credentials here.  A Gemini file is deleted in every
    terminal path once the service has received its resource name.
    """

    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval_seconds: float = 1.0,
        processing_timeout_seconds: float = 120.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("Gemini API key is required")
        if not model:
            raise ValueError("Gemini model is required")
        if poll_interval_seconds <= 0 or processing_timeout_seconds <= 0:
            raise ValueError("Gemini video polling values must be positive")
        self._api_key = api_key
        self.model = model
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self._transport = transport
        self._poll_interval = poll_interval_seconds
        self._processing_timeout = processing_timeout_seconds
        self._sleeper = sleeper
        self._monotonic = monotonic

    async def analyze_proxy(
        self, *, chunk: PreparedVideoChunk, prompt: str, response_json_schema: Mapping[str, Any], media_resolution: str
    ) -> GeminiVideoGeneration:
        self._validate_chunk(chunk)
        if media_resolution not in _MEDIA_RESOLUTIONS:
            raise ValueError("Gemini video media resolution is invalid")
        uploaded: GeminiUploadedVideo | None = None
        primary_error: BaseException | None = None
        cleanup_ok = True
        try:
            uploaded = await self._upload(chunk)
            active = await self._wait_until_active(uploaded)
            result = await self._generate(active, prompt, response_json_schema, media_resolution)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            if uploaded is not None:
                cleanup_ok = await self._delete_safely(uploaded.name, primary_error)
        if not cleanup_ok:
            return GeminiVideoGeneration(
                document=result.document, provider=result.provider, model=result.model,
                provider_request_id=result.provider_request_id, usage=result.usage,
                provider_metadata={**dict(result.provider_metadata), "temporary_file_deleted": False},
            )
        return result

    @staticmethod
    def _validate_chunk(chunk: PreparedVideoChunk) -> None:
        path = chunk.path
        try:
            stat = path.stat()
        except OSError as exc:
            raise AiProviderError("Prepared video proxy is unavailable.", code="gemini_video_proxy_missing", retryable=False) from exc
        if not path.is_file() or stat.st_size <= 0 or stat.st_size != chunk.size_bytes:
            raise AiProviderError("Prepared video proxy is invalid.", code="gemini_video_proxy_invalid", retryable=False)

    async def _upload(self, chunk: PreparedVideoChunk) -> GeminiUploadedVideo:
        headers = {
            "x-goog-api-key": self._api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(chunk.size_bytes),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json",
        }
        start = await self._request("POST", f"{_UPLOAD_ROOT}/files", headers=headers, json={"file": {"mimeType": "video/mp4"}})
        session_url = start.headers.get("X-Goog-Upload-URL")
        if not session_url:
            raise AiProviderError("Gemini did not create a video upload session.", code="gemini_video_upload_session_missing", retryable=True)
        finalize = await self._request(
            "POST", session_url,
            headers={
                "x-goog-api-key": self._api_key,
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Length": str(chunk.size_bytes),
                "Content-Type": "video/mp4",
            },
            content=self._file_chunks(chunk.path),
        )
        return self._uploaded_from_payload(self._json_object(finalize))

    async def _wait_until_active(self, uploaded: GeminiUploadedVideo) -> GeminiUploadedVideo:
        current = uploaded
        deadline = self._monotonic() + self._processing_timeout
        while True:
            state = current.state.upper()
            if state == "ACTIVE":
                return current
            if state == "FAILED":
                raise AiProviderError("Gemini failed to process the video.", code="gemini_video_processing_failed", retryable=False)
            if state not in {"PROCESSING", "", "STATE_UNSPECIFIED"}:
                raise AiProviderError("Gemini returned an unsupported video file state.", code="gemini_video_processing_state_invalid", retryable=False)
            if self._monotonic() >= deadline:
                raise AiProviderError("Gemini video processing timed out.", code="gemini_video_processing_timeout", retryable=True)
            await self._sleeper(self._poll_interval)
            response = await self._request("GET", f"{_API_ROOT}/{current.name}", headers={"x-goog-api-key": self._api_key})
            current = self._uploaded_from_payload(self._json_object(response))

    async def _generate(self, uploaded: GeminiUploadedVideo, prompt: str, schema: Mapping[str, Any], media_resolution: str) -> GeminiVideoGeneration:
        body = {
            "contents": [{"role": "user", "parts": [
                {"file_data": {"mime_type": "video/mp4", "file_uri": uploaded.uri}},
                {"text": prompt},
            ]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": dict(schema), "mediaResolution": media_resolution},
        }
        response = await self._request("POST", f"{_API_ROOT}/models/{self.model}:generateContent", headers={"x-goog-api-key": self._api_key}, json=body)
        payload = self._json_object(response)
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if isinstance(part, Mapping)).strip()
            document = json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AiProviderError("Gemini returned malformed video JSON.", code="gemini_video_invalid_json", retryable=False) from exc
        if not isinstance(document, Mapping):
            raise AiProviderError("Gemini video metadata root must be an object.", code="gemini_video_invalid_json", retryable=False)
        candidate = (payload.get("candidates") or [{}])[0]
        return GeminiVideoGeneration(
            document=dict(document), provider=self.provider_name,
            model=payload.get("modelVersion") or self.model, provider_request_id=payload.get("responseId"),
            usage=dict(payload.get("usageMetadata") or {}),
            provider_metadata={"finish_reason": candidate.get("finishReason"), "model_version": payload.get("modelVersion"), "temporary_file_deleted": True},
        )

    async def _delete_safely(self, name: str, primary_error: BaseException | None) -> bool:
        try:
            await self._request("DELETE", f"{_API_ROOT}/{name}", headers={"x-goog-api-key": self._api_key})
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.warning("gemini_video_temporary_file_delete_failed", extra={"file_name": name})
            if primary_error is not None:
                return False
            return False

    async def _request(self, method: str, url: str, *, headers: Mapping[str, str], **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(method, url, headers=dict(headers), **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AiProviderError("Gemini video request could not be completed.", code="gemini_video_transport_error", retryable=True) from exc
        if response.status_code >= 400:
            status = response.status_code
            codes = {400: ("gemini_video_bad_request", False), 401: ("gemini_video_authentication_error", False), 403: ("gemini_video_permission_denied", False), 429: ("gemini_video_rate_limited", True)}
            code, retryable = codes.get(status, ("gemini_video_http_error", status >= 500))
            raise AiProviderError(
                f"Gemini video request failed with HTTP {status}.",
                code=code, retryable=retryable, status_code=status,
                details=self._error_details(response),
            )
        return response

    def _error_details(self, response: httpx.Response) -> dict[str, Any]:
        payload: Mapping[str, Any] = {}
        try:
            value = response.json()
            if isinstance(value, Mapping):
                payload = value
        except ValueError:
            pass
        error = payload.get("error")
        error = error if isinstance(error, Mapping) else {}
        request_id = next((
            response.headers.get(header)
            for header in ("x-goog-request-id", "x-request-id", "x-cloud-trace-context")
            if response.headers.get(header)
        ), None)
        try:
            endpoint_path = urlsplit(str(response.url)).path
        except Exception:
            endpoint_path = "/v1beta"
        excerpt = json.dumps({"error": dict(error)}, ensure_ascii=False, sort_keys=True) if error else response.text
        return {
            "http_status": response.status_code,
            "endpoint_path": self._sanitize_error_text(endpoint_path),
            "google_error_status": self._sanitize_error_text(error.get("status")),
            "google_error_message": self._sanitize_error_text(error.get("message")),
            "provider_request_id": self._sanitize_error_text(request_id),
            "provider_response_excerpt": self._sanitize_error_text(excerpt),
        }

    def _sanitize_error_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).replace(self._api_key, "[REDACTED]")
        text = re.sub(
            r'(?i)("?(?:api[_-]?key|authorization|key)"?\s*[:=]\s*"?)[^\s,;"]+',
            r"\1[REDACTED]",
            text,
        )
        return text[:_MAX_ERROR_DETAIL_CHARS]

    @staticmethod
    async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
        with path.open("rb") as source:
            while block := source.read(_UPLOAD_BLOCK_SIZE):
                yield block

    @staticmethod
    def _json_object(response: httpx.Response) -> Mapping[str, Any]:
        try:
            value = response.json()
        except ValueError as exc:
            raise AiProviderError("Gemini returned an invalid response.", code="gemini_video_invalid_response", retryable=False) from exc
        if not isinstance(value, Mapping):
            raise AiProviderError("Gemini returned an invalid response object.", code="gemini_video_invalid_response", retryable=False)
        return value

    @staticmethod
    def _uploaded_from_payload(payload: Mapping[str, Any]) -> GeminiUploadedVideo:
        file = payload.get("file", payload)
        if not isinstance(file, Mapping) or not isinstance(file.get("name"), str) or not isinstance(file.get("uri"), str):
            raise AiProviderError("Gemini upload response omitted file identity.", code="gemini_video_upload_invalid_response", retryable=True)
        return GeminiUploadedVideo(name=file["name"], uri=file["uri"], mime_type=str(file.get("mimeType") or "video/mp4"), state=str(file.get("state") or ""))
