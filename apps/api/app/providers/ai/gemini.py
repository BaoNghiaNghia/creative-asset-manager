from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataAnalysisResult,
    AiProviderError,
)


class GeminiAiMetadataProvider:
    """Gemini REST adapter. Domain code never depends on a Google AI SDK."""

    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash",
        timeout_seconds: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("Gemini API key is required")
        if not model:
            raise ValueError("Gemini model is required")
        self._api_key = api_key
        self.model = model
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10))
        self._transport = transport

    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        self._check_cancelled(input)
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": input.prompt},
                    {"inlineData": {
                        "mimeType": input.image_mime_type,
                        "data": base64.b64encode(input.image_bytes).decode("ascii"),
                    }},
                ],
            }],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    url,
                    headers={"x-goog-api-key": self._api_key},
                    json=body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AiProviderError(
                "Gemini request could not be completed.",
                code="gemini_transport_error",
                retryable=True,
            ) from exc
        self._check_cancelled(input)
        self._raise_for_status(response)
        payload = self._response_object(response)
        text = self._candidate_text(payload)
        try:
            metadata = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AiProviderError(
                "Gemini returned malformed JSON.",
                code="gemini_invalid_json",
                retryable=True,
            ) from exc
        if not isinstance(metadata, Mapping):
            raise AiProviderError(
                "Gemini metadata root must be an object.",
                code="gemini_invalid_document",
                retryable=True,
            )
        candidate = (payload.get("candidates") or [{}])[0]
        return AiMetadataAnalysisResult(
            metadata=dict(metadata),
            provider=self.provider_name,
            model=payload.get("modelVersion") or self.model,
            provider_request_id=payload.get("responseId"),
            usage=dict(payload.get("usageMetadata") or {}),
            provider_metadata={
                "finish_reason": candidate.get("finishReason"),
                "model_version": payload.get("modelVersion"),
            },
            raw_response=payload,
        )

    @staticmethod
    def _response_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AiProviderError(
                "Gemini returned a non-JSON response.",
                code="gemini_invalid_response",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise AiProviderError(
                "Gemini returned an invalid response object.",
                code="gemini_invalid_response",
                retryable=True,
            )
        return payload

    @staticmethod
    def _candidate_text(payload: Mapping[str, Any]) -> str:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, Mapping)
            ).strip()
        except (KeyError, IndexError, TypeError):
            text = ""
        if not text:
            raise AiProviderError(
                "Gemini returned no metadata document.",
                code="gemini_empty_response",
                retryable=True,
            )
        return text

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        retryable = (
            response.status_code in {408, 409, 425, 429}
            or response.status_code >= 500
        )
        raise AiProviderError(
            f"Gemini request failed with HTTP {response.status_code}.",
            code="gemini_rate_limited" if response.status_code == 429 else "gemini_http_error",
            retryable=retryable,
        )

    @staticmethod
    def _check_cancelled(input: AiMetadataAnalysisInput) -> None:
        if input.is_cancelled is not None and input.is_cancelled():
            raise AiProviderError(
                "Gemini analysis was cancelled.",
                code="analysis_cancelled",
                retryable=True,
            )
