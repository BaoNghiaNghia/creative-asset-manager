from __future__ import annotations
import hashlib, json, re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from app.modules.inventory.daily_sheet.config import DailySheetConfig, normalize_identifier, normalize_sku

A1_ROWS = re.compile(r"^(?:'[^']+'|[^!]+)!([A-Z]+)([1-9][0-9]*):([A-Z]+)([1-9][0-9]*)$")

class DailySheetValidationError(ValueError):
    code = "invalid_inventory_data"

@dataclass(frozen=True, slots=True)
class StockRecord:
    warehouse: str
    sku: str
    quantity: Decimal

def canonical_hash(value: Any) -> str:
    def default(item):
        if isinstance(item, Decimal): return format(item, "f")
        if isinstance(item, (date, datetime)): return item.isoformat()
        raise TypeError(type(item).__name__)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def value_blocks(ranges: list[dict]) -> list[list[list[Any]]]:
    return [list(item.get("values") or []) for item in ranges]

def parse_decimal(value: Any, allow_negative: bool = False) -> Decimal:
    try:
        result = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise DailySheetValidationError("invalid quantity") from exc
    if not result.is_finite() or (result < 0 and not allow_negative):
        raise DailySheetValidationError("quantity outside policy")
    return result

def parse_stock_records(config: DailySheetConfig, ranges: list[dict]) -> dict[tuple[str, str], StockRecord]:
    if len(ranges) != len(config.source_ranges):
        raise DailySheetValidationError("incomplete source ranges")
    output: dict[tuple[str, str], StockRecord] = {}
    errors: list[str] = []
    for source, block in zip(config.source_ranges, ranges, strict=True):
        rows = list(block.get("values") or [])
        header_index = source.header_row - 1
        if header_index >= len(rows):
            errors.append(f"{source.a1_range}: missing header")
            continue
        headers = [normalize_identifier(str(value)) for value in rows[header_index]]
        required = [
            normalize_identifier(source.sku_column),
            normalize_identifier(source.quantity_column),
            normalize_identifier(source.warehouse_column),
        ]
        try:
            sku_index, quantity_index, warehouse_index = [headers.index(item) for item in required]
        except ValueError:
            errors.append(f"{source.a1_range}: missing required header")
            continue
        for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
            if not any(str(value).strip() for value in row):
                continue
            cell = lambda index: row[index] if index < len(row) else ""
            sku = normalize_sku(cell(sku_index))
            warehouse = normalize_identifier(str(cell(warehouse_index)))
            if not sku or not warehouse:
                errors.append(f"{source.a1_range} row {row_number}: missing key")
                continue
            try:
                quantity = parse_decimal(cell(quantity_index), config.allow_negative_quantity)
            except DailySheetValidationError:
                errors.append(f"{source.a1_range} row {row_number}: invalid quantity")
                continue
            key = (warehouse, sku)
            if key in output:
                errors.append(f"{source.a1_range} row {row_number}: duplicate warehouse/SKU")
                continue
            output[key] = StockRecord(warehouse, sku, quantity)
    if errors:
        raise DailySheetValidationError("; ".join(errors[:20]))
    return output

def build_variances(current, previous) -> list[dict[str, Any]]:
    output = []
    for key in sorted(set(current).union(previous)):
        record = current.get(key) or previous[key]
        previous_quantity = previous[key].quantity if key in previous else Decimal(0)
        current_quantity = current[key].quantity if key in current else Decimal(0)
        output.append({
            "warehouse": record.warehouse,
            "sku": record.sku,
            "previous_quantity": previous_quantity,
            "current_quantity": current_quantity,
            "variance": current_quantity - previous_quantity,
        })
    return output
