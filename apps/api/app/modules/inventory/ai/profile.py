from __future__ import annotations

from typing import Any

INVENTORY_EXTRACTION_PROFILE = "inventory-stock-sheet"
INVENTORY_EXTRACTION_PROFILE_VERSION = "v1"
INVENTORY_PROMPT_VERSION = "inventory-stock-sheet-prompt-v1"
INVENTORY_SCHEMA_VERSION = "inventory-stock-sheet-schema-v1"

INVENTORY_EXTRACTION_PROMPT = """Extract the inventory sheet faithfully. Return JSON only. Preserve raw item text and quantities; do not normalize names, units, or make approval decisions. Include document_type, business_date, location, page_number, page_count, and raw_item_lines. Each line may include raw_item_name, whole_quantity, whole_unit, fraction_quantity, fraction_unit, waste_quantity, waste_reason, and confidence."""

INVENTORY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["document_type", "page_number", "page_count", "raw_item_lines"],
    "properties": {
        "document_type": {"type": ["string", "null"]},
        "business_date": {"type": ["string", "null"]},
        "location": {"type": ["string", "null"]},
        "page_number": {"type": "integer", "minimum": 1},
        "page_count": {"type": "integer", "minimum": 1},
        "raw_item_lines": {"type": "array", "items": {"type": "object"}},
    },
}


def validate_extraction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("inventory_ai_invalid_structured_output")
    for name in ("document_type", "page_number", "page_count", "raw_item_lines"):
        if name not in value:
            raise ValueError("inventory_ai_invalid_structured_output")
    if not isinstance(value["page_number"], int) or value["page_number"] < 1:
        raise ValueError("inventory_ai_invalid_structured_output")
    if not isinstance(value["page_count"], int) or value["page_count"] < 1:
        raise ValueError("inventory_ai_invalid_structured_output")
    if not isinstance(value["raw_item_lines"], list) or any(not isinstance(line, dict) for line in value["raw_item_lines"]):
        raise ValueError("inventory_ai_invalid_structured_output")
    return value
