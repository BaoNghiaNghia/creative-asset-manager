from __future__ import annotations

import asyncio
import inspect
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.materials import normalize_material_text
from app.modules.inventory.persistence_model import (
    InventoryMaterialCandidateModel,
    InventorySettingsModel,
)
from app.providers.google.auth import get_connection_access_token


_UNIT_HEADERS = {
    "dvt",
    "don vi",
    "don vi tinh",
    "unit",
    "uom",
    "unit of measure",
}
_MASS_UNITS = {"g", "gr", "gram", "grams", "kg", "kilogram", "kilograms"}
_VOLUME_UNITS = {
    "ml",
    "milliliter",
    "milliliters",
    "millilitre",
    "millilitres",
    "l",
    "lit",
    "liter",
    "liters",
    "litre",
    "litres",
}
_COUNT_UNITS = {
    "count",
    "cai",
    "chai",
    "goi",
    "lon",
    "hop",
    "phan",
    "bich",
    "thung",
    "ly",
    "vien",
    "que",
    "pcs",
    "pc",
    "piece",
    "pieces",
}
_A1_CELL_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


class MaterialEvidenceError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _column_number(letters: str) -> int:
    value = 0
    for char in letters.upper():
        if char < "A" or char > "Z":
            raise ValueError("invalid_a1_column")
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def _column_letters(number: int) -> str:
    value = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _cell_parts(cell: str) -> tuple[int, int] | None:
    match = _A1_CELL_RE.fullmatch(str(cell or "").strip())
    if not match:
        return None
    return _column_number(match.group(1)), int(match.group(2))


def _matrix_cells(
    sheet: str,
    values: list[list[Any]],
    *,
    start_row: int = 1,
    start_column: int = 1,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for row_offset, row in enumerate(values):
        for column_offset, raw_value in enumerate(row):
            column = start_column + column_offset
            row_number = start_row + row_offset
            cell = f"{_column_letters(column)}{row_number}"
            cells.append(
                {
                    "sheet": sheet,
                    "cell": cell,
                    "raw_value": raw_value,
                    "evidence_hash": canonical_hash([raw_value]),
                }
            )
    return cells


def _normalize_evidence_text(value: object) -> str:
    # Vietnamese đ/Đ does not decompose under NFD, while the shared catalog
    # normalizer intentionally keeps its existing behavior. Normalize it only
    # for workbook header/unit evidence so labels such as "ĐVT" and
    # "Đơn vị tính" can be recognized without changing material identity rules.
    return normalize_material_text(str(value or "").replace("Đ", "D").replace("đ", "d"))


def _dimension_for_unit(value: object) -> str | None:
    normalized = _normalize_evidence_text(value)
    if normalized in _MASS_UNITS:
        return "mass"
    if normalized in _VOLUME_UNITS:
        return "volume"
    if normalized in _COUNT_UNITS:
        return "count"
    return None


def build_material_review_evidence(
    *,
    raw_name: str,
    name_evidence: Mapping[str, Any],
    cells: list[Mapping[str, Any]],
) -> dict[str, Any]:
    sheet = str(name_evidence.get("sheet") or "").strip()
    name_cell = str(name_evidence.get("cell") or "").strip().upper()
    expected_hash = str(name_evidence.get("evidence_hash") or "").strip()
    parts = _cell_parts(name_cell)
    if not sheet or parts is None:
        return {"status": "invalid_name_evidence", "unit_evidence": []}
    name_column, source_row = parts
    by_cell = {
        str(item.get("cell") or "").strip().upper(): item
        for item in cells
        if str(item.get("sheet") or "").strip() == sheet
    }
    observed_name = by_cell.get(name_cell)
    if (
        observed_name is None
        or str(observed_name.get("raw_value") or "").strip() != raw_name
        or (
            expected_hash
            and str(observed_name.get("evidence_hash") or "") != expected_hash
        )
    ):
        return {
            "status": "name_evidence_mismatch",
            "name_cell": name_cell,
            "unit_evidence": [],
        }

    unit_headers: dict[int, Mapping[str, Any]] = {}
    for item in cells:
        if str(item.get("sheet") or "").strip() != sheet:
            continue
        cell = str(item.get("cell") or "").strip().upper()
        cell_parts = _cell_parts(cell)
        if cell_parts is None:
            continue
        column, row = cell_parts
        if row >= source_row:
            continue
        header = _normalize_evidence_text(item.get("raw_value"))
        if header in _UNIT_HEADERS:
            previous = unit_headers.get(column)
            previous_parts = (
                _cell_parts(str(previous.get("cell") or ""))
                if previous is not None
                else None
            )
            if previous_parts is None or row > previous_parts[1]:
                unit_headers[column] = item

    evidence: list[dict[str, Any]] = []
    for column, header in sorted(unit_headers.items()):
        value_cell = f"{_column_letters(column)}{source_row}"
        value_item = by_cell.get(value_cell)
        raw_unit = value_item.get("raw_value") if value_item is not None else None
        if raw_unit is None or not str(raw_unit).strip():
            continue
        evidence.append(
            {
                "header_cell": str(header.get("cell") or "").upper(),
                "header": str(header.get("raw_value") or ""),
                "value_cell": value_cell,
                "value": str(raw_unit).strip(),
                "evidence_hash": (
                    str(value_item.get("evidence_hash") or "")
                    if value_item is not None
                    else ""
                ),
            }
        )

    result: dict[str, Any] = {
        "status": "explicit_unit_found" if evidence else "no_explicit_unit_found",
        "name_cell": name_cell,
        "name_evidence_hash": expected_hash,
        "unit_evidence": evidence,
    }
    if len(evidence) == 1:
        raw_unit = evidence[0]["value"]
        result["suggested_preferred_unit"] = raw_unit
        dimension = _dimension_for_unit(raw_unit)
        if dimension:
            result["suggested_canonical_dimension"] = dimension
    return result


class MaterialCandidateEvidenceService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        client_factory: Callable[..., Any] = GoogleSheetsInventoryClient,
        token_resolver: Callable[..., Any] = get_connection_access_token,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.token_resolver = token_resolver

    def _token(self, connection_id: str) -> str:
        value = self.token_resolver(connection_id)
        return asyncio.run(value) if inspect.isawaitable(value) else str(value)

    def refresh_pending(
        self,
        tenant_id: str,
        *,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            settings = session.scalar(
                select(InventorySettingsModel).where(
                    InventorySettingsModel.tenant_id == tenant_id,
                    InventorySettingsModel.enabled.is_(True),
                    InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
                )
            )
            source = (
                session.scalar(
                    select(ExternalSourceModel).where(
                        ExternalSourceModel.tenant_id == tenant_id,
                        ExternalSourceModel.id
                        == (settings.external_source_id if settings else ""),
                    )
                )
                if settings
                else None
            )
            if settings is None or source is None:
                raise MaterialEvidenceError("material_evidence_configuration_incomplete")
            connection_id = str(
                source.oauth_connection_id
                or (source.source_metadata or {}).get("oauth_connection_id")
                or ""
            )
            if not connection_id:
                raise MaterialEvidenceError("material_evidence_google_connection_missing")

            query = select(InventoryMaterialCandidateModel).where(
                InventoryMaterialCandidateModel.tenant_id == tenant_id,
                InventoryMaterialCandidateModel.status.in_(
                    ("new_material", "possible_rename", "ambiguous")
                ),
            )
            if candidate_id:
                query = query.where(InventoryMaterialCandidateModel.id == candidate_id)
            rows = list(
                session.scalars(
                    query.order_by(
                        InventoryMaterialCandidateModel.created_at,
                        InventoryMaterialCandidateModel.id,
                    )
                )
            )
            descriptors: list[dict[str, Any]] = []
            for row in rows:
                context = row.context_json if isinstance(row.context_json, dict) else {}
                if context.get("origin") != "carry_forward_missing_catalog_item":
                    continue
                descriptors.append(
                    {
                        "id": row.id,
                        "source_id": row.source_id,
                        "sheet": row.sheet,
                        "source_row": int(row.source_row),
                        "raw_name": row.raw_name,
                        "name_evidence": dict(context.get("name_evidence") or {}),
                    }
                )

        if candidate_id and not descriptors:
            raise MaterialEvidenceError("material_candidate_not_refreshable")
        if not descriptors:
            return {"updated": 0, "statuses": {}}

        token = self._token(connection_id)
        updates: dict[str, dict[str, Any]] = {}
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for descriptor in descriptors:
            groups[(descriptor["source_id"], descriptor["sheet"])].append(descriptor)

        with self.client_factory(token) as google:
            for (spreadsheet_id, sheet), group in groups.items():
                escaped_sheet = sheet.replace("'", "''")
                first_row = min(item["source_row"] for item in group)
                header_end = min(max(first_row - 1, 1), 10)
                unique_rows = sorted({item["source_row"] for item in group})
                ranges = [f"'{escaped_sheet}'!A1:AZ{header_end}"] + [
                    f"'{escaped_sheet}'!A{row_number}:AZ{row_number}"
                    for row_number in unique_rows
                ]
                blocks = google.batch_get_values(
                    spreadsheet_id,
                    ranges,
                    value_render_option="UNFORMATTED_VALUE",
                )
                header_values = (
                    list(blocks[0].get("values") or []) if blocks else []
                )
                header_cells = _matrix_cells(sheet, header_values)
                row_cells: dict[int, list[dict[str, Any]]] = {}
                for index, row_number in enumerate(unique_rows, start=1):
                    values = (
                        list(blocks[index].get("values") or [])
                        if index < len(blocks)
                        else []
                    )
                    row_cells[row_number] = _matrix_cells(
                        sheet,
                        values,
                        start_row=row_number,
                    )
                for descriptor in group:
                    evidence = build_material_review_evidence(
                        raw_name=descriptor["raw_name"],
                        name_evidence=descriptor["name_evidence"],
                        cells=header_cells
                        + row_cells.get(descriptor["source_row"], []),
                    )
                    evidence["refreshed_at"] = datetime.now(timezone.utc).isoformat()
                    updates[descriptor["id"]] = evidence

        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(InventoryMaterialCandidateModel).where(
                        InventoryMaterialCandidateModel.tenant_id == tenant_id,
                        InventoryMaterialCandidateModel.id.in_(list(updates)),
                    )
                )
            )
            for row in rows:
                context = dict(row.context_json or {})
                context["review_evidence"] = updates[row.id]
                row.context_json = context
            session.commit()

        statuses: dict[str, int] = defaultdict(int)
        for evidence in updates.values():
            statuses[str(evidence.get("status") or "unknown")] += 1
        return {"updated": len(updates), "statuses": dict(statuses)}
