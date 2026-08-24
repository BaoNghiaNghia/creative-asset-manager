from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Callable

import base64
import json

import httpx

from app.modules.inventory.credentials import InventoryCredentialError, InventoryGeminiCredentialResolver


class InventoryAiGatewayError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class InventoryAiGatewayResult:
    raw_response_json: Mapping[str, Any]
    extracted_json: Mapping[str, Any]
    provider_request_id: str | None = None
    usage_json: Mapping[str, Any] = field(default_factory=dict)
    estimated_cost_micros: int = 0


class InventoryAiGateway(Protocol):
    def analyze(self, *, tenant_id: str, image_bytes: bytes, image_mime_type: str, prompt: str, schema: Mapping[str, Any], provider: str, model: str) -> InventoryAiGatewayResult: ...


class DisabledInventoryAiGateway:
    def analyze(self, **_kwargs: Any) -> InventoryAiGatewayResult:
        raise InventoryAiGatewayError("inventory_ai_gateway_unconfigured", retryable=False)


class RuntimeInventoryGeminiGateway:
    """Inventory-only Gemini REST boundary resolving the current tenant key per call."""

    def __init__(self, resolver: InventoryGeminiCredentialResolver, *, timeout_seconds: float = 45.0, request: Callable[..., Mapping[str, Any]] | None = None, text_request: Callable[..., Mapping[str, Any]] | None = None):
        self.resolver = resolver
        self.timeout_seconds = timeout_seconds
        self._request = request or self._request_gemini
        self._text_request = text_request or self._request_gemini_text

    def analyze(self, *, tenant_id: str, image_bytes: bytes, image_mime_type: str, prompt: str, schema: Mapping[str, Any], provider: str, model: str) -> InventoryAiGatewayResult:
        if provider != "gemini":
            raise InventoryAiGatewayError("inventory_ai_provider_unsupported", retryable=False)
        try:
            secret = self.resolver.resolve(tenant_id)
        except InventoryCredentialError as exc:
            raise InventoryAiGatewayError(str(exc), retryable=False) from exc
        try:
            payload = self._request(secret, image_bytes, image_mime_type, prompt, schema, model)
        except httpx.TimeoutException as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        extracted = payload.get("extracted_json") if isinstance(payload, Mapping) else None
        if not isinstance(extracted, Mapping):
            raise InventoryAiGatewayError("inventory_gemini_invalid_response", retryable=False)
        return InventoryAiGatewayResult(raw_response_json=dict(payload), extracted_json=dict(extracted))

    def analyze_structured_text(self, *, tenant_id: str, prompt: str, schema: Mapping[str, Any], provider: str, model: str) -> InventoryAiGatewayResult:
        if provider != "gemini":
            raise InventoryAiGatewayError("inventory_ai_provider_unsupported", retryable=False)
        try:
            secret = self.resolver.resolve(tenant_id)
            payload = self._text_request(secret, prompt, schema, model)
        except InventoryCredentialError as exc:
            raise InventoryAiGatewayError(str(exc), retryable=False) from exc
        except httpx.TimeoutException as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        extracted = payload.get("extracted_json") if isinstance(payload, Mapping) else None
        if not isinstance(extracted, Mapping):
            raise InventoryAiGatewayError("inventory_gemini_invalid_response", retryable=False)
        return InventoryAiGatewayResult(raw_response_json=dict(payload), extracted_json=dict(extracted))

    @staticmethod
    def _generation_config(schema: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "responseMimeType": "application/json",
            "responseJsonSchema": deepcopy(dict(schema)),
        }

    @staticmethod
    def _raise_provider_status(status_code: int) -> None:
        if status_code < 400:
            return
        if status_code == 400:
            code, retryable = "inventory_gemini_invalid_request", False
        elif status_code in {401, 403}:
            code, retryable = "inventory_gemini_auth_or_permission_error", False
        elif status_code == 404:
            code, retryable = "inventory_gemini_model_not_found", False
        elif status_code == 429:
            code, retryable = "inventory_gemini_rate_limited", True
        else:
            code = "inventory_gemini_request_failed"
            retryable = status_code >= 500
        raise InventoryAiGatewayError(code, retryable=retryable)

    @staticmethod
    def _structured_result(response: httpx.Response) -> Mapping[str, Any]:
        RuntimeInventoryGeminiGateway._raise_provider_status(response.status_code)
        try:
            raw = response.json()
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            extracted = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InventoryAiGatewayError(
                "inventory_gemini_invalid_response", retryable=False
            ) from exc
        if not isinstance(extracted, Mapping):
            raise InventoryAiGatewayError(
                "inventory_gemini_invalid_response", retryable=False
            )
        return {"provider_response": raw, "extracted_json": dict(extracted)}

    def _request_gemini_text(
        self, secret: str, prompt: str, schema: Mapping[str, Any], model: str
    ) -> Mapping[str, Any]:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": secret},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": self._generation_config(schema),
            },
            timeout=self.timeout_seconds,
        )
        return self._structured_result(response)

    def _request_gemini(
        self,
        secret: str,
        image_bytes: bytes,
        image_mime_type: str,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
    ) -> Mapping[str, Any]:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": secret},
            json={
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": image_mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }],
                "generationConfig": self._generation_config(schema),
            },
            timeout=self.timeout_seconds,
        )
        return self._structured_result(response)
