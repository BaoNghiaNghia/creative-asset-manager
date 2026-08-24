from __future__ import annotations
import hashlib, json, re
from typing import Any
from .contracts import WorkbookSnapshot
_RANGE_RE = re.compile(r"^(?P<sheet>'(?:[^']|'')+'|[^!]+)!(?P<c1>[A-Z]+)(?P<r1>[1-9][0-9]*):(?P<c2>[A-Z]+)(?P<r2>[1-9][0-9]*)$")
def _column_number(value: str) -> int:
    result = 0
    for character in value: result = result * 26 + ord(character) - 64
    return result
def _column_name(value: int) -> str:
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26); result = chr(65 + remainder) + result
    return result
def canonical_source_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
def build_workbook_snapshot(*, spreadsheet_file_id: str, file_metadata: dict[str, Any], spreadsheet_metadata: dict[str, Any], requested_range: str, raw_block: dict[str, Any], formula_block: dict[str, Any]) -> WorkbookSnapshot:
    match = _RANGE_RE.fullmatch(requested_range)
    if match is None: raise ValueError("inventory_sheet_agent_invalid_source_range")
    sheet_title = match.group("sheet").strip("'").replace("''", "'")
    sheets = list(spreadsheet_metadata.get("sheets") or [])
    sheet = next((item for item in sheets if str((item.get("properties") or {}).get("title") or "") == sheet_title), None)
    if sheet is None: raise ValueError("inventory_sheet_agent_sheet_not_found")
    raw_values = [list(row) for row in (raw_block.get("values") or [])]; formulas = [list(row) for row in (formula_block.get("values") or [])]
    start_column, start_row = _column_number(match.group("c1")), int(match.group("r1"))
    row_count = max(len(raw_values), len(formulas)); column_count = max([0] + [len(row) for row in raw_values] + [len(row) for row in formulas])
    coordinates = [[f"{_column_name(start_column + column)}{start_row + row}" for column in range(column_count)] for row in range(row_count)]
    properties, sheet_properties = spreadsheet_metadata.get("properties") or {}, sheet.get("properties") or {}
    evidence = {"spreadsheet_file_id": spreadsheet_file_id, "spreadsheet_title": str(properties.get("title") or file_metadata.get("name") or ""), "workbook_timezone": str(properties.get("timeZone") or ""), "sheet_id": sheet_properties.get("sheetId"), "sheet_title": sheet_title, "requested_range": requested_range, "raw_values": raw_values, "formulas": formulas, "coordinates": coordinates, "merged_ranges": [json.dumps(value, sort_keys=True, separators=(",", ":")) if isinstance(value, dict) else str(value) for value in (sheet.get("merges") or [])], "protected_ranges": [json.dumps(value, sort_keys=True, separators=(",", ":")) if isinstance(value, dict) else str(value) for value in (sheet.get("protectedRanges") or [])], "source_modified_time": file_metadata.get("modifiedTime")}
    return WorkbookSnapshot(**evidence, source_hash=canonical_source_hash(evidence))
def snapshot_cell(snapshot: WorkbookSnapshot, cell: str, *, formulas: bool = False):
    for row_index, row in enumerate(snapshot.coordinates):
        for column_index, coordinate in enumerate(row):
            if coordinate == cell:
                source = snapshot.formulas if formulas else snapshot.raw_values
                if row_index >= len(source) or column_index >= len(source[row_index]): return None
                return source[row_index][column_index]
    raise KeyError(cell)
