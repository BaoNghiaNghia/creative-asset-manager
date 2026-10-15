from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Callable

import base64
import hashlib
import json
import logging

import httpx

from app.modules.inventory.credentials import InventoryCredentialError, InventoryGeminiCredentialResolver

logger = logging.getLogger(__name__)


class InventoryAiGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        provider_status: int | None = None,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.provider_status = provider_status
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class InventoryAiGatewayResult:
    raw_response_json: Mapping[str, Any]
    extracted_json: Mapping[str, Any]
    provider_request_id: str | None = None
    usage_json: Mapping[str, Any] = field(default_factory=dict)
    estimated_cost_micros: int | None = None


@dataclass(frozen=True, slots=True)
class InventoryGeminiToolCall:
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class InventoryGeminiToolTurn:
    content: Mapping[str, Any]
    calls: tuple[InventoryGeminiToolCall, ...]


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
        # Cache only candidate-set fingerprints and preferred positions, never keys.
        # Resolver still runs on every request to honor rotations and revocations.
        self._preferred_credential: dict[str, tuple[str, int]] = {}

    def _with_credential_fallback(self, tenant_id: str, call: Callable[[str], Any]) -> Any:
        try:
            # Look up the declared method on the class. Mock resolvers can
            # invent arbitrary attributes, including an unusable candidates().
            candidates = getattr(type(self.resolver), "resolve_candidates", None)
            secrets = (
                candidates(self.resolver, tenant_id) if callable(candidates)
                else (self.resolver.resolve(tenant_id),)
            )
        except InventoryCredentialError as exc:
            raise InventoryAiGatewayError(str(exc), retryable=False) from exc
        # One selected Inventory key and at most five shared Image/Video keys.
        # Never fall back on 429, model errors or bad payloads. A successful
        # authorization fallback is reused for subsequent Gemini tool rounds.
        attempts = tuple(secrets[:6])
        fingerprint = hashlib.sha256(
            b"".join(len(value.encode("utf-8")).to_bytes(4, "big")
                     + value.encode("utf-8") for value in attempts)
        ).hexdigest()
        preferred = self._preferred_credential.get(tenant_id)
        start = preferred[1] if preferred and preferred[0] == fingerprint else 0
        indices = (start, *(i for i in range(len(attempts)) if i != start))
        for position, index in enumerate(indices):
            try:
                result = call(attempts[index])
                self._preferred_credential[tenant_id] = (fingerprint, index)
                return result
            except InventoryAiGatewayError as exc:
                if exc.code != "inventory_gemini_auth_or_permission_error" or position == len(indices) - 1:
                    if self._preferred_credential.get(tenant_id) == (fingerprint, index):
                        self._preferred_credential.pop(tenant_id, None)
                    raise
                logger.warning(
                    "inventory_gemini_credential_fallback tenant_id=%s attempt=%d",
                    tenant_id, position + 1,
                )
        raise InventoryAiGatewayError("inventory_gemini_credential_unavailable", retryable=False)

    def analyze(self, *, tenant_id: str, image_bytes: bytes, image_mime_type: str, prompt: str, schema: Mapping[str, Any], provider: str, model: str) -> InventoryAiGatewayResult:
        if provider != "gemini":
            raise InventoryAiGatewayError("inventory_ai_provider_unsupported", retryable=False)
        try:
            payload = self._with_credential_fallback(
                tenant_id,
                lambda secret: self._request(
                    secret, image_bytes, image_mime_type, prompt, schema, model
                ),
            )
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
            payload = self._with_credential_fallback(
                tenant_id,
                lambda secret: self._text_request(secret, prompt, schema, model),
            )
        except httpx.TimeoutException as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        extracted = payload.get("extracted_json") if isinstance(payload, Mapping) else None
        if not isinstance(extracted, Mapping):
            raise InventoryAiGatewayError("inventory_gemini_invalid_response", retryable=False)
        return InventoryAiGatewayResult(raw_response_json=dict(payload), extracted_json=dict(extracted))

    def generate_tool_turn(
        self,
        *,
        tenant_id: str,
        contents: list[Mapping[str, Any]],
        function_declarations: list[Mapping[str, Any]],
        provider: str,
        model: str,
        force_function_name: str | None = None,
    ) -> InventoryGeminiToolTurn:
        if provider != "gemini":
            raise InventoryAiGatewayError("inventory_ai_provider_unsupported", retryable=False)
        try:
            def send(secret: str) -> httpx.Response:
                response = httpx.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": secret},
                    json={
                        "contents": deepcopy(contents),
                        "tools": [{"functionDeclarations": deepcopy(function_declarations)}],
                        "toolConfig": {
                            "functionCallingConfig": (
                                {"mode": "ANY", "allowedFunctionNames": [force_function_name]}
                                if force_function_name else {"mode": "AUTO"}
                            )
                        },
                    },
                    timeout=self.timeout_seconds,
                )
                self._raise_provider_status(
                    response.status_code,
                    retry_after_seconds=self._provider_retry_after_seconds(response),
                )
                return response

            response = self._with_credential_fallback(tenant_id, send)
            raw = response.json()
            content = raw["candidates"][0]["content"]
            parts = content.get("parts") or []
            calls = tuple(
                InventoryGeminiToolCall(
                    name=str(part["functionCall"]["name"]),
                    arguments=dict(part["functionCall"].get("args") or {}),
                )
                for part in parts
                if isinstance(part, Mapping) and isinstance(part.get("functionCall"), Mapping)
            )
        except InventoryCredentialError as exc:
            raise InventoryAiGatewayError(str(exc), retryable=False) from exc
        except httpx.TimeoutException as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise InventoryAiGatewayError("inventory_gemini_transport_error", retryable=True) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InventoryAiGatewayError("inventory_gemini_invalid_response", retryable=False) from exc
        return InventoryGeminiToolTurn(content=dict(content), calls=calls)

    @staticmethod
    def _generation_config(schema: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "responseMimeType": "application/json",
            "responseJsonSchema": deepcopy(dict(schema)),
        }

    @staticmethod
    def _provider_retry_after_seconds(response: httpx.Response) -> float | None:
        delays: list[float] = []
        headers = getattr(response, "headers", {}) or {}
        raw_header = headers.get("Retry-After") or headers.get("retry-after")
        if raw_header:
            try:
                delays.append(float(raw_header))
            except (TypeError, ValueError):
                pass
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, Mapping):
            error = payload.get("error")
            details = error.get("details") if isinstance(error, Mapping) else None
            if isinstance(details, list):
                for detail in details:
                    if not isinstance(detail, Mapping):
                        continue
                    retry_delay = detail.get("retryDelay")
                    if isinstance(retry_delay, str) and retry_delay.endswith("s"):
                        try:
                            delays.append(float(retry_delay[:-1]))
                        except ValueError:
                            pass
        valid = [value for value in delays if value > 0]
        return min(max(valid), 120.0) if valid else None

    @staticmethod
    def _raise_provider_status(
        status_code: int, *, retry_after_seconds: float | None = None
    ) -> None:
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
        raise InventoryAiGatewayError(
            code,
            retryable=retryable,
            provider_status=status_code,
            retry_after_seconds=retry_after_seconds,
        )

    @staticmethod
    def _structured_result(response: httpx.Response) -> Mapping[str, Any]:
        RuntimeInventoryGeminiGateway._raise_provider_status(
            response.status_code,
            retry_after_seconds=RuntimeInventoryGeminiGateway._provider_retry_after_seconds(response),
        )
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
