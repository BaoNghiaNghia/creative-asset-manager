from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

import pytest

from app.modules.inventory.ai.gateway import (
    InventoryAiGatewayError,
    RuntimeInventoryGeminiGateway,
)
from app.modules.inventory.daily_sheet.semantic import (
    MATERIAL_SCHEMA,
    QUANTITY_SCHEMA,
    SCHEMA_MAPPING_SCHEMA,
)


class Resolver:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, tenant_id: str) -> str:
        self.calls.append(tenant_id)
        return "test-secret-never-logged"


class Response:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


def successful_response(extracted: dict[str, Any]) -> Response:
    return Response(
        200,
        {
            "candidates": [{
                "content": {
                    "parts": [{"text": json.dumps(extracted)}],
                }
            }]
        },
    )


def test_structured_text_uses_full_response_json_schema_without_mutation(monkeypatch):
    resolver = Resolver()
    gateway = RuntimeInventoryGeminiGateway(resolver)
    schema = {
        "type": "object",
        "properties": {
            "fixed": {"type": ["string", "null"]},
            "dynamic": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["fixed", "dynamic"],
        "additionalProperties": False,
    }
    original = deepcopy(schema)
    captured: dict[str, Any] = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return successful_response({"fixed": None, "dynamic": {"closing": "H"}})

    monkeypatch.setattr("app.modules.inventory.ai.gateway.httpx.post", post)
    result = gateway.analyze_structured_text(
        tenant_id="tenant-a",
        prompt="map headers",
        schema=schema,
        provider="gemini",
        model="gemini-test",
    )

    generation = captured["json"]["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseJsonSchema"] == original
    assert "responseSchema" not in generation
    assert generation["responseJsonSchema"]["additionalProperties"] is False
    assert generation["responseJsonSchema"]["properties"]["dynamic"][
        "additionalProperties"
    ] == {"type": "string"}
    assert generation["responseJsonSchema"]["properties"]["fixed"]["type"] == [
        "string",
        "null",
    ]
    assert result.extracted_json == {"fixed": None, "dynamic": {"closing": "H"}}
    assert schema == original
    assert resolver.calls == ["tenant-a"]


def test_image_structured_path_uses_response_json_schema_and_extracts_json(monkeypatch):
    resolver = Resolver()
    gateway = RuntimeInventoryGeminiGateway(resolver)
    schema = {"type": "object", "additionalProperties": False}
    captured: dict[str, Any] = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return successful_response({"document_type": "stock_count"})

    monkeypatch.setattr("app.modules.inventory.ai.gateway.httpx.post", post)
    result = gateway.analyze(
        tenant_id="tenant-a",
        image_bytes=b"image",
        image_mime_type="image/jpeg",
        prompt="analyze image",
        schema=schema,
        provider="gemini",
        model="gemini-test",
    )

    generation = captured["json"]["generationConfig"]
    assert generation == {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }
    assert "responseSchema" not in generation
    assert result.extracted_json == {"document_type": "stock_count"}
    assert captured["json"]["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"


@pytest.mark.parametrize(
    "schema",
    [QUANTITY_SCHEMA, SCHEMA_MAPPING_SCHEMA, MATERIAL_SCHEMA],
    ids=["quantity", "schema-mapping", "material"],
)
def test_real_daily_sheet_schemas_are_forwarded_unchanged(monkeypatch, schema):
    resolver = Resolver()
    gateway = RuntimeInventoryGeminiGateway(resolver)
    original = deepcopy(schema)
    captured: dict[str, Any] = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return successful_response({"ok": True})

    monkeypatch.setattr("app.modules.inventory.ai.gateway.httpx.post", post)
    gateway.analyze_structured_text(
        tenant_id="tenant-a",
        prompt="structured",
        schema=schema,
        provider="gemini",
        model="gemini-test",
    )

    forwarded = captured["json"]["generationConfig"]["responseJsonSchema"]
    assert forwarded == original
    assert schema == original
    assert forwarded is not schema


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (400, "inventory_gemini_invalid_request", False),
        (401, "inventory_gemini_auth_or_permission_error", False),
        (403, "inventory_gemini_auth_or_permission_error", False),
        (404, "inventory_gemini_model_not_found", False),
        (429, "inventory_gemini_rate_limited", True),
        (500, "inventory_gemini_request_failed", True),
        (503, "inventory_gemini_request_failed", True),
    ],
)
def test_structured_http_errors_are_safe_and_classified(
    monkeypatch, status_code, code, retryable
):
    resolver = Resolver()
    gateway = RuntimeInventoryGeminiGateway(resolver)

    monkeypatch.setattr(
        "app.modules.inventory.ai.gateway.httpx.post",
        lambda *_args, **_kwargs: Response(
            status_code,
            {"error": {"message": "sensitive provider details"}},
        ),
    )

    with pytest.raises(InventoryAiGatewayError) as captured:
        gateway.analyze_structured_text(
            tenant_id="tenant-a",
            prompt="structured",
            schema={"type": "object"},
            provider="gemini",
            model="gemini-test",
        )

    assert captured.value.code == code
    assert captured.value.retryable is retryable
    assert str(captured.value) == code
    assert "sensitive" not in str(captured.value)
    assert "test-secret" not in str(captured.value)
