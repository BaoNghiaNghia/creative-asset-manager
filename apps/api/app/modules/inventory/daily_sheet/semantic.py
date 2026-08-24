from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.modules.inventory.ai.gateway import (
    InventoryAiGatewayError,
    RuntimeInventoryGeminiGateway,
)
from app.modules.inventory.credentials import InventoryGeminiCredentialResolver
from app.modules.inventory.model import InventoryAiControlModel


QUANTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["parsed", "ambiguous", "malformed", "suspected_shift"]},
        "raw": {"type": "string"},
        "canonical_value": {"type": ["string", "null"]},
        "canonical_unit": {"type": ["string", "null"], "enum": ["count", "g", "ml", None]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "requires_review": {"type": "boolean"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "raw", "canonical_value", "canonical_unit", "confidence", "requires_review", "warnings"],
    "additionalProperties": False,
}

SCHEMA_MAPPING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["mapped", "ambiguous", "major_drift"]},
        "mapping": {"type": "object", "additionalProperties": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "requires_review": {"type": "boolean"},
        "reset_relevant_changed": {"type": "boolean"},
        "changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "mapping", "confidence", "requires_review", "reset_relevant_changed", "changes"],
    "additionalProperties": False,
}

MATERIAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "material_id": {"type": ["string", "null"]},
        "suggested_canonical_name": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["material_id", "suggested_canonical_name", "confidence", "reasons"],
    "additionalProperties": False,
}


class InventoryDailySheetSemanticAnalyzer:
    """Read-only semantic fallback. It never owns Google or persistence writes."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: RuntimeInventoryGeminiGateway,
        *,
        enabled: bool,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.enabled = enabled

    def _runtime(self, tenant_id: str) -> tuple[str, str] | None:
        if not self.enabled:
            return None
        with self.session_factory() as session:
            control = session.scalar(
                select(InventoryAiControlModel).where(
                    InventoryAiControlModel.tenant_id == tenant_id
                )
            )
        if control is None or not control.enabled or control.emergency_stop:
            return None
        models = tuple(str(value) for value in (control.allowed_models_json or ()) if value)
        if control.provider != "gemini" or not models:
            return None
        return control.provider, models[0]

    def _analyze(
        self, tenant_id: str, operation: str, payload: Mapping[str, Any], schema: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        runtime = self._runtime(tenant_id)
        if runtime is None:
            return None
        provider, model = runtime
        prompt = (
            "You are the Inventory Daily Sheet semantic fallback. Return JSON only, "
            "strictly matching the response schema. Analyze only supplied evidence. "
            "Never invent missing quantities, mappings, materials, units, or package "
            "conversions. Never request or perform writes.\n"
            f"Operation: {operation}\nPayload: "
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        )
        try:
            return self.gateway.analyze_structured_text(
                tenant_id=tenant_id,
                prompt=prompt,
                schema=schema,
                provider=provider,
                model=model,
            ).extracted_json
        except InventoryAiGatewayError:
            return None

    def analyze_quantity(
        self, tenant_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        result = self._analyze(tenant_id, "quantity_interpretation", payload, QUANTITY_SCHEMA)
        if result is None:
            return None
        required = {
            "status", "raw", "canonical_value", "canonical_unit",
            "confidence", "requires_review", "warnings",
        }
        if set(result) != required:
            return None
        if result["status"] not in {"parsed", "ambiguous", "malformed", "suspected_shift"}:
            return None
        if str(result["raw"]) != str(payload.get("raw", "")):
            return None
        try:
            confidence = Decimal(str(result["confidence"]))
        except (InvalidOperation, ValueError):
            return None
        if confidence < 0 or confidence > 1 or not isinstance(result["requires_review"], bool):
            return None
        if not isinstance(result["warnings"], list) or not all(
            isinstance(value, str) for value in result["warnings"]
        ):
            return None
        if result["status"] == "parsed":
            if result["canonical_unit"] not in {"count", "g", "ml"}:
                return None
            try:
                value = Decimal(str(result["canonical_value"]))
            except (InvalidOperation, ValueError):
                return None
            if not value.is_finite() or value < 0:
                return None
        elif result["canonical_value"] is not None or result["canonical_unit"] is not None:
            return None
        return dict(result)

    def analyze_schema(
        self, tenant_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        result = self._analyze(tenant_id, "schema_header_mapping", payload, SCHEMA_MAPPING_SCHEMA)
        if result is None:
            return None
        required = {
            "status", "mapping", "confidence", "requires_review",
            "reset_relevant_changed", "changes",
        }
        if set(result) != required or result["status"] not in {"mapped", "ambiguous", "major_drift"}:
            return None
        if not isinstance(result["mapping"], Mapping):
            return None
        if not isinstance(result["requires_review"], bool) or not isinstance(
            result["reset_relevant_changed"], bool
        ):
            return None
        try:
            confidence = Decimal(str(result["confidence"]))
        except (InvalidOperation, ValueError):
            return None
        if confidence < 0 or confidence > 1:
            return None
        if not isinstance(result["changes"], list) or not all(
            isinstance(value, str) for value in result["changes"]
        ):
            return None
        return dict(result)

    def match_material(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        tenant_id = str(payload.get("tenant_id") or "")
        if not tenant_id:
            return None
        result = self._analyze(tenant_id, "material_match", payload, MATERIAL_SCHEMA)
        if result is None:
            return None
        required = {"material_id", "suggested_canonical_name", "confidence", "reasons"}
        if set(result) != required or not isinstance(result["suggested_canonical_name"], str):
            return None
        try:
            confidence = Decimal(str(result["confidence"]))
        except (InvalidOperation, ValueError):
            return None
        if confidence < 0 or confidence > 1:
            return None
        if not isinstance(result["reasons"], list) or not all(
            isinstance(value, str) for value in result["reasons"]
        ):
            return None
        return dict(result)


def build_daily_sheet_semantic_analyzer(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> InventoryDailySheetSemanticAnalyzer:
    runtime_settings = settings or get_settings()
    gateway = RuntimeInventoryGeminiGateway(
        InventoryGeminiCredentialResolver(session_factory, runtime_settings),
        timeout_seconds=runtime_settings.INVENTORY_AI_TIMEOUT_SECONDS,
    )
    return InventoryDailySheetSemanticAnalyzer(
        session_factory, gateway, enabled=runtime_settings.INVENTORY_AI_ENABLED
    )
