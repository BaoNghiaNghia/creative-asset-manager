from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


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
    def analyze(self, *, image_bytes: bytes, image_mime_type: str, prompt: str, schema: Mapping[str, Any], provider: str, model: str) -> InventoryAiGatewayResult: ...


class DisabledInventoryAiGateway:
    def analyze(self, **_kwargs: Any) -> InventoryAiGatewayResult:
        raise InventoryAiGatewayError("inventory_ai_gateway_unconfigured", retryable=False)
