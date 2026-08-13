from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Callable

import base64

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

    def __init__(self, resolver: InventoryGeminiCredentialResolver, *, timeout_seconds: float = 45.0, request: Callable[..., Mapping[str, Any]] | None = None):
        self.resolver = resolver
        self.timeout_seconds = timeout_seconds
        self._request = request or self._request_gemini

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

    def _request_gemini(self, secret: str, image_bytes: bytes, image_mime_type: str, prompt: str, schema: Mapping[str, Any], model: str) -> Mapping[str, Any]:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": secret},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}, {"inlineData": {"mimeType": image_mime_type, "data": base64.b64encode(image_bytes).decode("ascii")}}]}], "generationConfig": {"responseMimeType": "application/json", "responseSchema": dict(schema)}},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise InventoryAiGatewayError("inventory_gemini_request_failed", retryable=response.status_code >= 500 or response.status_code == 429)
        return response.json()
