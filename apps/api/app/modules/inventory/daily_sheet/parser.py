from __future__ import annotations
import hashlib, json, re, unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from app.modules.inventory.daily_sheet.config import DailyCountSheetConfig, DailySheetConfig, normalize_identifier, normalize_sku
from app.modules.inventory.materials import normalize_material_text

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

@dataclass(frozen=True, slots=True)
class InventoryQuantityComponent:
    label: str | None
    value: Decimal
    unit: str
    raw: str


@dataclass(frozen=True, slots=True)
class InventoryQuantity:
    raw: str
    canonical_value: Decimal
    canonical_unit: str
    components: tuple[InventoryQuantityComponent, ...]
    parse_status: str = "parsed"


@dataclass(frozen=True, slots=True)
class DailyCountRecord:
    warehouse: str
    item_key: str
    item_name: str
    category: str
    source_row: int
    quantity: InventoryQuantity
    source_cells: tuple[str, ...] = ()


class DailyCountSheetValidationError(DailySheetValidationError):
    def __init__(self, errors: list[dict[str, Any]], warnings: list[dict[str, Any]] | None = None):
        self.errors = errors
        self.warnings = warnings or []
        self.code = errors[0]["code"] if errors else "invalid_daily_count_sheet"
        super().__init__(self.code)


_QUANTITY_ATOM = re.compile(r"^(?P<number>[0-9]+(?:[.,][0-9]+)?)(?P<unit>g|ml)?$", re.IGNORECASE)
_LITER_SHORTHAND = re.compile(r"^(?P<liters>[0-9]+)l(?P<tenths>[0-9])$", re.IGNORECASE)
_CONTAINER = re.compile(r"^(?P<label>[0-9]+)\((?P<amount>[^()]+)\)$")
_PACKAGE_ATOM = re.compile(r"^(?P<number>[0-9]+(?:[.,][0-9]+)?)\s*(?P<package>[^0-9+;()]+)$")

# Missing evidence is never sent to Gemini. Structural corruption may be
# explained by Gemini for review, but can never authorize a parsed quantity.
_QUANTITY_SEMANTIC_SKIP_CODES = frozenset({"blank_authoritative_quantity"})
_QUANTITY_SEMANTIC_HARD_BLOCK_CODES = frozenset({
    "blank_authoritative_quantity",
    "suspected_shifted_quantity",
    "unmatched_quantity_parenthesis",
    "incompatible_quantity_units",
    "unknown_package_conversion",
})


def _quantity_atom(raw: str) -> InventoryQuantityComponent:
    value = raw.strip().lower().replace(" ", "")
    shorthand = _LITER_SHORTHAND.fullmatch(value)
    if shorthand:
        amount = (Decimal(shorthand.group("liters")) + Decimal(shorthand.group("tenths")) / 10) * 1000
        return InventoryQuantityComponent(None, amount, "ml", raw)
    match = _QUANTITY_ATOM.fullmatch(value)
    if not match:
        if any(character.isalpha() for character in value):
            code = "unsupported_quantity_unit"
        elif "," in value and not re.fullmatch(r"[0-9]+,[0-9]+(?:g|ml)?", value):
            code = "malformed_decimal_comma"
        else:
            code = "malformed_quantity"
        raise DailySheetValidationError(code)
    return InventoryQuantityComponent(
        None,
        Decimal(match.group("number").replace(",", ".")),
        (match.group("unit") or "count").lower(),
        raw,
    )


def parse_inventory_quantity(
    raw: object,
    package_conversions: Mapping[str, tuple[Decimal, str]] | None = None,
) -> InventoryQuantity:
    text_value = str(raw if raw is not None else "").strip()
    if not text_value:
        raise DailySheetValidationError("blank_authoritative_quantity")
    if text_value.count("(") != text_value.count(")") or text_value.startswith(")") or text_value.endswith("("):
        raise DailySheetValidationError("unmatched_quantity_parenthesis")
    conversions = package_conversions or {}
    parts = [part.strip() for part in re.split(r"[;+]", text_value)]
    if any(not part for part in parts):
        raise DailySheetValidationError("malformed_quantity")
    components: list[InventoryQuantityComponent] = []
    for part in parts:
        container = _CONTAINER.fullmatch(part)
        if container:
            atom = _quantity_atom(container.group("amount"))
            components.append(InventoryQuantityComponent(container.group("label"), atom.value, atom.unit, part))
            continue
        try:
            components.append(_quantity_atom(part))
            continue
        except DailySheetValidationError:
            package = _PACKAGE_ATOM.fullmatch(part)
            if package is None:
                raise
            package_name = normalize_material_text(package.group("package"))
            conversion = conversions.get(package_name)
            if conversion is None:
                raise DailySheetValidationError("unknown_package_conversion")
            package_value, package_unit = conversion
            multiplier = Decimal(package.group("number").replace(",", "."))
            components.append(InventoryQuantityComponent(package.group("package").strip(), multiplier * package_value, package_unit, part))
    units = {component.unit for component in components}
    if len(units) != 1:
        raise DailySheetValidationError("incompatible_quantity_units")
    unit = next(iter(units))
    return InventoryQuantity(text_value, sum((item.value for item in components), Decimal(0)), unit, tuple(components))


def _column_letters(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _normalized_header(value: object) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).strip()).casefold()


def _normalized_item_key(value: object) -> str | None:
    text_value = str(value or "").strip()
    if isinstance(value, (int, float, Decimal)) and Decimal(str(value)) == Decimal(str(value)).to_integral():
        return str(int(Decimal(str(value))))
    if re.fullmatch(r"[0-9]+", text_value):
        return str(int(text_value))
    return None


def classify_daily_count_row(row: list[Any], *, key_index: int, name_index: int) -> str:
    relevant = [str(value or "").strip() for value in row]
    if not any(relevant):
        return "EMPTY"
    name = _normalized_header(row[name_index] if name_index < len(row) else "")
    if name.startswith("t\u1ed5ng"):
        return "TOTAL"
    key = row[key_index] if key_index < len(row) else ""
    if _normalized_item_key(key) is not None:
        return "ITEM"
    if str(key or "").strip():
        return "SECTION"
    return "EMPTY"


def parse_daily_count_records(
    config: DailyCountSheetConfig,
    value_range: dict[str, Any],
    *,
    package_conversion_resolver: Any | None = None,
    quantity_semantic_analyzer: Any | None = None,
    schema_semantic_analyzer: Any | None = None,
) -> tuple[dict[tuple[str, str], DailyCountRecord], list[dict[str, Any]]]:
    rows = list(value_range.get("values") or [])
    if len(rows) < config.source.header_row:
        raise DailyCountSheetValidationError([{"code": "header_row_missing", "sheet": config.source.sheet, "row": config.source.header_row}])
    header = rows[config.source.header_row - 1]
    header_positions: dict[str, list[int]] = {}
    for index, value in enumerate(header):
        header_positions.setdefault(_normalized_header(value), []).append(index)
    column_names = config.source.columns.model_dump()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for semantic, heading in column_names.items():
        matches = header_positions.get(_normalized_header(heading), [])
        if len(matches) != 1:
            errors.append({"code": "required_header_missing" if not matches else "duplicate_header", "sheet": config.source.sheet, "row": config.source.header_row, "column": semantic, "raw_value": heading})
        else:
            indexes[semantic] = matches[0]
    expected_layout = list(range(len(column_names)))
    actual_layout = [indexes.get(semantic) for semantic in column_names]
    layout_drift = not errors and actual_layout != expected_layout
    reset_relevant_semantics = sorted(set(
        config.reset.entry_columns
        + config.reset.clear_columns
        + [config.reset.carry_forward_from, config.reset.carry_forward_to]
    ))
    deterministic_reset_change = any(
        indexes.get(semantic) != expected_layout[position]
        for position, semantic in enumerate(column_names)
        if semantic in reset_relevant_semantics
    )
    if (errors or layout_drift) and schema_semantic_analyzer:
        proposal = schema_semantic_analyzer({
            "sheet": config.source.sheet,
            "header_row": config.source.header_row,
            "actual_headers": [str(value) for value in header],
            "approved_mapping": column_names,
            "layout_drift": layout_drift,
            "actual_layout": actual_layout,
            "expected_layout": expected_layout,
            "reset_relevant_semantics": reset_relevant_semantics,
        })
        mapping = proposal.get("mapping") if isinstance(proposal, Mapping) else None
        proposed_indexes: dict[str, int] = {}
        if isinstance(mapping, Mapping) and set(mapping) == set(column_names):
            for semantic, heading in mapping.items():
                matches = header_positions.get(_normalized_header(heading), [])
                if len(matches) == 1:
                    proposed_indexes[semantic] = matches[0]
        if (
            isinstance(proposal, Mapping)
            and proposal.get("status") == "mapped"
            and Decimal(str(proposal.get("confidence", 0))) >= Decimal("0.98")
            and len(proposed_indexes) == len(column_names)
        ):
            indexes = proposed_indexes
            warnings.append({
                "code": "schema_mapping_proposed",
                "sheet": config.source.sheet,
                "confidence": proposal.get("confidence"),
                "changes": list(proposal.get("changes") or ()),
                "requires_review": deterministic_reset_change or bool(proposal.get("requires_review")),
                "reset_relevant_changed": deterministic_reset_change or bool(proposal.get("reset_relevant_changed")),
                "proposed_mapping": dict(mapping),
            })
            errors = []
        else:
            errors.append({
                "code": "schema_semantic_mapping_rejected",
                "sheet": config.source.sheet,
                "proposal": dict(proposal) if isinstance(proposal, Mapping) else None,
            })
    elif layout_drift:
        errors.append({
            "code": "schema_semantic_mapping_rejected",
            "sheet": config.source.sheet,
            "proposal": None,
        })
    if errors:
        raise DailyCountSheetValidationError(errors, warnings)
    records: dict[tuple[str, str], DailyCountRecord] = {}
    business_columns = ("opening", "used", "inbound", "waste", "closing")
    warehouse = normalize_identifier(config.source.warehouse)
    for offset, row in enumerate(rows[config.source.header_row:], start=config.source.header_row + 1):
        kind = classify_daily_count_row(row, key_index=indexes["item_key"], name_index=indexes["name"])
        if kind != "ITEM":
            continue
        item_key = _normalized_item_key(row[indexes["item_key"]] if indexes["item_key"] < len(row) else "")
        item_name = str(row[indexes["name"]] if indexes["name"] < len(row) else "").strip()
        category = str(row[indexes["category"]] if indexes["category"] < len(row) else "").strip()
        base = {"sheet": config.source.sheet, "row": offset, "item_key": item_key, "item_name": item_name}
        if not item_name:
            errors.append({**base, "code": "missing_item_name", "cell": f"{_column_letters(indexes['name'])}{offset}", "column": "name", "raw_value": ""})
            continue
        key = (warehouse, str(item_key))
        if key in records:
            errors.append({**base, "code": "duplicate_item_key", "cell": f"{_column_letters(indexes['item_key'])}{offset}", "column": "item_key", "raw_value": item_key})
            continue
        parsed: dict[str, InventoryQuantity] = {}
        for position, semantic in enumerate(business_columns):
            index = indexes[semantic]
            raw = row[index] if index < len(row) else ""
            if str(raw or "").strip() == "" and semantic != config.stock.authoritative_column:
                continue
            try:
                conversions = (
                    package_conversion_resolver(
                        item_key=str(item_key), item_name=item_name, category=category,
                        source_row=offset, sheet=config.source.sheet,
                    )
                    if package_conversion_resolver else None
                )
                parsed[semantic] = parse_inventory_quantity(raw, conversions)
            except DailySheetValidationError as exc:
                code = str(exc)
                next_semantic = business_columns[position + 1] if position + 1 < len(business_columns) else None
                next_raw = row[indexes[next_semantic]] if next_semantic and indexes[next_semantic] < len(row) else ""
                if ("(" in str(raw) and ")" not in str(raw) and ")" in str(next_raw)) or (str(raw).startswith(")") and position > 0):
                    code = "suspected_shifted_quantity"
                cell_address = f"{_column_letters(index)}{offset}"
                proposal = None
                if (
                    quantity_semantic_analyzer
                    and code not in _QUANTITY_SEMANTIC_SKIP_CODES
                ):
                    nearby = [{
                        "semantic": nearby_semantic,
                        "cell": f"{_column_letters(indexes[nearby_semantic])}{offset}",
                        "raw": str(row[indexes[nearby_semantic]]) if indexes[nearby_semantic] < len(row) else "",
                    } for nearby_semantic in business_columns]
                    proposal = quantity_semantic_analyzer({
                        "raw": str(raw), "cell": cell_address, "item_name": item_name,
                        "category": category, "item_key": str(item_key), "sheet": config.source.sheet,
                        "nearby_business_cells": nearby,
                        "approved_package_conversions": {
                            name: {"canonical_value": str(value), "canonical_unit": unit}
                            for name, (value, unit) in (conversions or {}).items()
                        },
                        "deterministic_error": code,
                    })
                mandatory_review = code in _QUANTITY_SEMANTIC_HARD_BLOCK_CODES
                if (
                    isinstance(proposal, Mapping)
                    and proposal.get("status") == "parsed"
                    and not proposal.get("requires_review")
                    and Decimal(str(proposal.get("confidence", 0))) >= Decimal("0.98")
                    and not mandatory_review
                ):
                    semantic_value = Decimal(str(proposal["canonical_value"]))
                    semantic_unit = str(proposal["canonical_unit"])
                    component = InventoryQuantityComponent("gemini_semantic_fallback", semantic_value, semantic_unit, str(raw))
                    parsed[semantic] = InventoryQuantity(str(raw), semantic_value, semantic_unit, (component,), "gemini_validated")
                    warnings.append({
                        **base, "code": "quantity_semantic_fallback",
                        "cell": cell_address, "column": semantic,
                        "confidence": proposal.get("confidence"),
                        "warnings": list(proposal.get("warnings") or ()),
                    })
                    continue
                error = {**base, "code": code, "cell": cell_address, "column": semantic, "raw_value": str(raw)}
                if isinstance(proposal, Mapping):
                    error["semantic_suggestion"] = dict(proposal)
                errors.append(error)
        authoritative = parsed.get(config.stock.authoritative_column)
        if authoritative is not None:
            records[key] = DailyCountRecord(
                warehouse, str(item_key), item_name, category, offset, authoritative,
                (f"{_column_letters(indexes[config.stock.authoritative_column])}{offset}",),
            )
    if errors:
        raise DailyCountSheetValidationError(errors, warnings)
    return records, warnings


def build_daily_count_variances(current: dict[tuple[str, str], DailyCountRecord], previous: dict[tuple[str, str], DailyCountRecord], *, name_change_policy: str = "warn") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for key in sorted(set(current) | set(previous)):
        now, before = current.get(key), previous.get(key)
        if now is None or before is None:
            status = "material_added" if before is None else "material_removed_or_missing"
            record = now or before
            warnings.append({"code": status, "warehouse": key[0], "item_key": key[1]})
            result.append({
                "material_status": status, "warehouse": key[0], "item_key": key[1],
                "item_name": record.item_name, "category": record.category,
                "previous_raw_quantity": before.quantity.raw if before else None,
                "current_raw_quantity": now.quantity.raw if now else None,
                "previous_canonical_quantity": before.quantity.canonical_value if before else None,
                "current_canonical_quantity": now.quantity.canonical_value if now else None,
                "unit": record.quantity.canonical_unit, "variance": None,
            })
            continue
        renamed = _normalized_header(now.item_name) != _normalized_header(before.item_name)
        if renamed:
            item = {"code": "material_renamed", "warehouse": key[0], "item_key": key[1], "previous_name": before.item_name, "current_name": now.item_name}
            if name_change_policy == "reject":
                raise DailyCountSheetValidationError([item])
            warnings.append(item)
        if now.quantity.canonical_unit != before.quantity.canonical_unit:
            raise DailyCountSheetValidationError([{"code": "incompatible_quantity_units", "warehouse": key[0], "item_key": key[1]}])
        variance = now.quantity.canonical_value - before.quantity.canonical_value
        result.append({
            "material_status": "material_renamed" if renamed else ("material_changed" if variance else "material_unchanged"),
            "warehouse": key[0], "item_key": key[1], "item_name": now.item_name, "category": now.category,
            "previous_raw_quantity": before.quantity.raw, "current_raw_quantity": now.quantity.raw,
            "previous_canonical_quantity": before.quantity.canonical_value,
            "current_canonical_quantity": now.quantity.canonical_value,
            "unit": now.quantity.canonical_unit, "variance": variance,
        })
    return result, warnings
