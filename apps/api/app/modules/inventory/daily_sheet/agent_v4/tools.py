from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.persistence_model import InventoryItemAliasModel, InventoryItemModel

from .contracts import (
    CellEvidence,
    EvidenceReference,
    KnowledgeProposal,
    StagedEdits,
    WorkbookAssessment,
)


_CELL_RE = re.compile(r"^\$?(?P<column>[A-Za-z]+)\$?(?P<row>[1-9][0-9]*)$")
_RANGE_RE = re.compile(
    r"^(?P<sheet>'(?:[^']|'')+'|[^!]+)!"
    r"(?P<start>\$?[A-Za-z]+\$?[1-9][0-9]*)"
    r"(?::(?P<end>\$?[A-Za-z]+\$?[1-9][0-9]*))?$"
)
_CORRECTABLE_TOOL_ERRORS = frozenset(
    {
        "invalid_a1_range", "reversed_a1_range", "sheet_range_mismatch",
        "invalid_cell", "cells_required", "missing_or_invalid_evidence",
        "assessment_incomplete",
        "grounded_assessment_required", "material_id_required",
        "duplicate_or_conflicting_target", "target_out_of_grid",
        "target_not_read", "formula_or_restricted_target", "blank_is_not_zero",
    }
)


class V4AgentSafetyError(RuntimeError):
    code = "inventory_sheet_agent_v4_safety_error"


class V4AgentLimitExceeded(V4AgentSafetyError):
    code = "inventory_sheet_agent_v4_limit_exceeded"


def _column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - 64
    return result


def _column_name(value: int) -> str:
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell_parts(value: str) -> tuple[int, int]:
    match = _CELL_RE.fullmatch(value)
    if match is None:
        raise V4AgentSafetyError("invalid_cell")
    return _column_number(match.group("column").upper()), int(match.group("row"))


def _canonical_cell(value: str) -> str:
    column, row = _cell_parts(value)
    return f"{_column_name(column)}{row}"


def _parse_range(sheet: str, a1_range: str) -> tuple[str, str, int, int, int, int]:
    candidate = re.sub(r"\s*([!:])\s*", r"\1", a1_range.strip())
    if "!" not in candidate:
        escaped = sheet.replace("'", "''")
        candidate = f"'{escaped}'!{candidate}"
    match = _RANGE_RE.fullmatch(candidate)
    if match is None:
        raise V4AgentSafetyError("invalid_a1_range")
    parsed_sheet = match.group("sheet")
    if parsed_sheet.startswith("'"):
        parsed_sheet = parsed_sheet[1:-1].replace("''", "'")
    start = _canonical_cell(match.group("start"))
    end = _canonical_cell(match.group("end") or match.group("start"))
    c1, r1 = _cell_parts(start)
    c2, r2 = _cell_parts(end)
    if c2 < c1 or r2 < r1:
        raise V4AgentSafetyError("reversed_a1_range")
    escaped_sheet = parsed_sheet.replace("'", "''")
    qualified = f"'{escaped_sheet}'!{start}" + (f":{end}" if end != start else "")
    return parsed_sheet, qualified, c1, r1, c2, r2


def _evidence_hash(sheet: str, cell: str, raw_value: Any, formula: str | None) -> str:
    payload = json.dumps(
        {"sheet": sheet, "cell": cell, "raw_value": raw_value, "formula": formula},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _grid_range_to_bounds(value: Mapping[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(value.get("startColumnIndex") or 0) + 1,
        int(value.get("startRowIndex") or 0) + 1,
        int(value.get("endColumnIndex") or 0),
        int(value.get("endRowIndex") or 0),
    )


class V4WorkbookToolHost:
    """Generic, tenant-bound workbook tools. No Inventory semantics live here."""

    def __init__(
        self,
        *,
        tenant_id: str,
        spreadsheet_file_id: str,
        allowed_sheets: list[str],
        google: Any,
        session_factory: sessionmaker[Session],
        max_read_calls: int,
        max_read_cells: int,
        max_edit_operations: int,
        allow_auto_transforms: bool = False,
    ) -> None:
        self.tenant_id = tenant_id
        self.spreadsheet_file_id = spreadsheet_file_id
        self.allowed_sheets = set(allowed_sheets)
        self.google = google
        self.session_factory = session_factory
        self.max_read_calls = max_read_calls
        self.max_read_cells = max_read_cells
        self.max_edit_operations = max_edit_operations
        self.allow_auto_transforms = allow_auto_transforms
        self.read_calls = 0
        self.read_cell_count = 0
        self.ledger: dict[tuple[str, str], CellEvidence] = {}
        self.assessment: WorkbookAssessment | None = None
        self.assessment_references: list[EvidenceReference] = []
        self.staged: StagedEdits | None = None
        self.knowledge_proposals: list[KnowledgeProposal] = []
        self.last_execution: dict[str, Any] = {}
        self.tool_trace: list[dict[str, Any]] = []
        self._metadata: dict[str, Any] | None = None
        self._modified_time: str | None = None

    def _metadata_payload(self) -> dict[str, Any]:
        file_metadata = self.google.validate_native_spreadsheet(self.spreadsheet_file_id)
        metadata = self.google.spreadsheet_metadata(self.spreadsheet_file_id)
        sheets = []
        for item in metadata.get("sheets") or []:
            properties = item.get("properties") or {}
            title = str(properties.get("title") or "")
            if self.allowed_sheets and title not in self.allowed_sheets:
                continue
            sheets.append(
                {
                    "sheet_id": properties.get("sheetId"),
                    "title": title,
                    "grid": {
                        "rows": (properties.get("gridProperties") or {}).get("rowCount"),
                        "columns": (properties.get("gridProperties") or {}).get("columnCount"),
                    },
                    "merged_ranges": deepcopy(item.get("merges") or []),
                    "protected_ranges": deepcopy(item.get("protectedRanges") or []),
                }
            )
        available = {item["title"] for item in sheets}
        missing = self.allowed_sheets - available
        if missing:
            raise V4AgentSafetyError("configured_sheet_not_found")
        self._metadata = {"properties": deepcopy(metadata.get("properties") or {}), "sheets": sheets}
        self._modified_time = str(file_metadata.get("modifiedTime") or "") or None
        return {
            "spreadsheet_file_id": self.spreadsheet_file_id,
            "title": file_metadata.get("name") or (metadata.get("properties") or {}).get("title"),
            "timezone": (metadata.get("properties") or {}).get("timeZone"),
            "modified_time": self._modified_time,
            "sheets": sheets,
        }

    def get_workbook_metadata(self, _arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._metadata_payload()

    def _authorize_sheet(self, sheet: str) -> None:
        if self._metadata is None:
            self._metadata_payload()
        available = {item["title"] for item in self._metadata["sheets"]}
        if sheet not in available or (self.allowed_sheets and sheet not in self.allowed_sheets):
            raise V4AgentSafetyError("sheet_not_authorized")

    def _consume_read(self, cells: int) -> None:
        self.read_calls += 1
        self.read_cell_count += cells
        if self.read_calls > self.max_read_calls or self.read_cell_count > self.max_read_cells:
            raise V4AgentLimitExceeded("workbook_read_limit_exceeded")

    def read_range(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        requested_sheet = str(arguments.get("sheet") or "")
        include_formulas = bool(arguments.get("include_formulas", True))
        sheet, qualified, c1, r1, c2, r2 = _parse_range(
            requested_sheet, str(arguments.get("a1_range") or "")
        )
        if requested_sheet and requested_sheet != sheet:
            raise V4AgentSafetyError("sheet_range_mismatch")
        self._authorize_sheet(sheet)
        cell_count = (c2 - c1 + 1) * (r2 - r1 + 1)
        self._consume_read(cell_count)
        raw_blocks = self.google.batch_get_values(
            self.spreadsheet_file_id, [qualified], value_render_option="UNFORMATTED_VALUE"
        )
        formula_blocks = (
            self.google.batch_get_values(
                self.spreadsheet_file_id, [qualified], value_render_option="FORMULA"
            )
            if include_formulas
            else []
        )
        raw_rows = list(raw_blocks[0].get("values") or []) if raw_blocks else []
        formula_rows = list(formula_blocks[0].get("values") or []) if formula_blocks else []
        cells: list[CellEvidence] = []
        for row in range(r1, r2 + 1):
            for column in range(c1, c2 + 1):
                row_offset, column_offset = row - r1, column - c1
                raw_value = (
                    raw_rows[row_offset][column_offset]
                    if row_offset < len(raw_rows) and column_offset < len(raw_rows[row_offset])
                    else None
                )
                formula_value = (
                    formula_rows[row_offset][column_offset]
                    if row_offset < len(formula_rows)
                    and column_offset < len(formula_rows[row_offset])
                    else None
                )
                formula = (
                    formula_value
                    if isinstance(formula_value, str) and formula_value.startswith("=")
                    else None
                )
                cell = f"{_column_name(column)}{row}"
                evidence = CellEvidence(
                    sheet=sheet,
                    cell=cell,
                    raw_value=raw_value,
                    formula=formula,
                    evidence_hash=_evidence_hash(sheet, cell, raw_value, formula),
                )
                self.ledger[(sheet, cell)] = evidence
                cells.append(evidence)
        return {
            "sheet": sheet,
            "range": qualified,
            "cells": [item.model_dump(mode="json") for item in cells],
        }

    def _read_exact_cells_batched(
        self,
        sheet: str,
        cells: list[str],
        *,
        consume_limits: bool = True,
    ) -> list[CellEvidence]:
        self._authorize_sheet(sheet)
        canonical_cells = [_canonical_cell(str(cell)) for cell in cells]
        if consume_limits:
            for _ in canonical_cells:
                self._consume_read(1)
        escaped_sheet = sheet.replace("'", "''")
        ranges = [f"'{escaped_sheet}'!{cell}" for cell in canonical_cells]
        raw_blocks = self.google.batch_get_values(
            self.spreadsheet_file_id,
            ranges,
            value_render_option="UNFORMATTED_VALUE",
        )
        formula_blocks = self.google.batch_get_values(
            self.spreadsheet_file_id,
            ranges,
            value_render_option="FORMULA",
        )
        evidence: list[CellEvidence] = []
        for index, cell in enumerate(canonical_cells):
            raw_rows = (
                list(raw_blocks[index].get("values") or [])
                if index < len(raw_blocks)
                else []
            )
            formula_rows = (
                list(formula_blocks[index].get("values") or [])
                if index < len(formula_blocks)
                else []
            )
            raw_value = raw_rows[0][0] if raw_rows and raw_rows[0] else None
            formula_value = (
                formula_rows[0][0] if formula_rows and formula_rows[0] else None
            )
            formula = (
                formula_value
                if isinstance(formula_value, str) and formula_value.startswith("=")
                else None
            )
            item = CellEvidence(
                sheet=sheet,
                cell=cell,
                raw_value=raw_value,
                formula=formula,
                evidence_hash=_evidence_hash(sheet, cell, raw_value, formula),
            )
            self.ledger[(sheet, cell)] = item
            evidence.append(item)
        return evidence

    def read_cells(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        sheet = str(arguments.get("sheet") or "")
        cells = [str(cell) for cell in list(arguments.get("cells") or [])]
        if not cells:
            raise V4AgentSafetyError("cells_required")
        evidence = self._read_exact_cells_batched(sheet, cells)
        return {
            "sheet": sheet,
            "cells": [item.model_dump(mode="json") for item in evidence],
        }

    def search_material_catalog(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        query_text = str(arguments.get("query") or "").strip()
        category = str(arguments.get("category") or "").strip()
        try:
            requested_limit = int(arguments.get("limit") or 25)
        except (TypeError, ValueError):
            requested_limit = 25
        limit = min(max(requested_limit, 1), 50)
        with self.session_factory() as session:
            query = select(InventoryItemModel).where(
                InventoryItemModel.tenant_id == self.tenant_id,
                InventoryItemModel.active.is_(True),
            )
            if category:
                query = query.where(
                    func.lower(func.coalesce(InventoryItemModel.category, ""))
                    == category.casefold()
                )
            if query_text:
                needle = f"%{query_text.casefold()}%"
                alias_ids = select(InventoryItemAliasModel.item_id).where(
                    InventoryItemAliasModel.tenant_id == self.tenant_id,
                    func.lower(InventoryItemAliasModel.normalized_alias).like(needle),
                )
                query = query.where(
                    or_(
                        func.lower(InventoryItemModel.sku).like(needle),
                        func.lower(InventoryItemModel.name).like(needle),
                        func.lower(func.coalesce(InventoryItemModel.category, "")).like(needle),
                        InventoryItemModel.id.in_(alias_ids),
                    )
                )
            rows = list(
                session.scalars(
                    query.order_by(InventoryItemModel.name, InventoryItemModel.id).limit(limit + 1)
                )
            )
            truncated = len(rows) > limit
            rows = rows[:limit]
            materials = [
                {
                    "material_id": row.id,
                    "sku": row.sku,
                    "name": row.name,
                    "category": row.category,
                    "base_unit": row.base_unit,
                    "preferred_unit": row.preferred_unit,
                }
                for row in rows
            ]
        return {
            "materials": materials,
            "query": query_text or None,
            "category": category or None,
            "limit": limit,
            "truncated": truncated,
        }

    def get_material_catalog(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        # Backward-compatible server handler for old in-flight tool calls. New
        # declarations expose only bounded search to avoid returning the entire catalog.
        return self.search_material_catalog(arguments)

    def _reference_evidence(self, reference: EvidenceReference) -> CellEvidence:
        evidence = self.ledger.get((reference.sheet, reference.cell))
        if evidence is None or evidence.evidence_hash != reference.evidence_hash:
            raise V4AgentSafetyError("missing_or_invalid_evidence")
        return evidence

    def submit_workbook_assessment(
        self, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        assessment = WorkbookAssessment.model_validate(dict(arguments))
        references: list[EvidenceReference] = []
        for observation in assessment.observations:
            references.extend(observation.evidence)
        for uncertainty in assessment.uncertainties:
            references.extend(uncertainty.evidence)
        for reference in references:
            self._reference_evidence(reference)
        self.assessment = assessment
        self.assessment_references = references
        return {
            "accepted": True,
            "grounded_observations": len(assessment.observations),
            "uncertainties": len(assessment.uncertainties),
            "additional_reads_needed": assessment.additional_reads_needed,
        }

    def propose_knowledge(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        proposal = KnowledgeProposal.model_validate(dict(arguments))
        for reference in proposal.evidence:
            self._reference_evidence(reference)
        canonical = proposal.model_dump(mode="json")
        if any(item.model_dump(mode="json") == canonical for item in self.knowledge_proposals):
            return {"accepted": True, "duplicate": True, "proposal_count": len(self.knowledge_proposals)}
        self.knowledge_proposals.append(proposal)
        return {
            "accepted": True,
            "duplicate": False,
            "proposal_count": len(self.knowledge_proposals),
            "instruction": "Proposal recorded for human review only. It is not active knowledge.",
        }

    def build_change_audit(self, verification_status: str) -> list[dict[str, Any]]:
        if self.staged is None:
            return []
        rows: list[dict[str, Any]] = []
        for operation in self.staged.operations:
            before = self.ledger.get((operation.sheet, operation.cell))
            source = self._reference_evidence(operation.copy_from) if operation.copy_from else None
            rows.append(
                {
                    "sheet": operation.sheet,
                    "cell": operation.cell,
                    "row_number": _cell_parts(operation.cell)[1],
                    "before": before.raw_value if before is not None else None,
                    "after": operation.value if operation.type == "set_cell" else None,
                    "source_sheet": source.sheet if source is not None else None,
                    "source_cell": source.cell if source is not None else None,
                    "material_id": (operation.semantic_context or {}).get("material_id"),
                    "warehouse_id": (operation.semantic_context or {}).get("warehouse_id"),
                    "operation_type": operation.type,
                    "reason": operation.reason,
                    "provenance": operation.provenance,
                    "evidence": [item.model_dump(mode="json") for item in operation.evidence],
                    "verification_status": verification_status,
                }
            )
        return rows

    def _target_within_grid(self, sheet: str, cell: str) -> bool:
        if self._metadata is None:
            self._metadata_payload()
        column, row = _cell_parts(cell)
        for item in self._metadata["sheets"]:
            if item["title"] == sheet:
                rows = item["grid"].get("rows")
                columns = item["grid"].get("columns")
                return (
                    isinstance(rows, int)
                    and isinstance(columns, int)
                    and row <= rows
                    and column <= columns
                )
        return False

    def _is_restricted_target(self, sheet: str, cell: str) -> bool:
        if self._metadata is None:
            self._metadata_payload()
        column, row = _cell_parts(cell)
        for item in self._metadata["sheets"]:
            if item["title"] != sheet:
                continue
            for merged in item["merged_ranges"]:
                c1, r1, c2, r2 = _grid_range_to_bounds(merged)
                if c1 <= column <= c2 and r1 <= row <= r2:
                    return True
            for protected in item["protected_ranges"]:
                grid = protected.get("range") or {}
                c1, r1, c2, r2 = _grid_range_to_bounds(grid)
                if c1 <= column <= c2 and r1 <= row <= r2:
                    return True
        return False

    def _validate_material_actions(self, staged: StagedEdits) -> bool:
        matched_ids = {
            action.material_id
            for action in staged.material_actions
            if action.action == "MATCH_EXISTING" and action.material_id
        }
        if any(action.action == "MATCH_EXISTING" and not action.material_id for action in staged.material_actions):
            raise V4AgentSafetyError("material_id_required")
        if matched_ids:
            with self.session_factory() as session:
                found = set(
                    session.scalars(
                        select(InventoryItemModel.id).where(
                            InventoryItemModel.tenant_id == self.tenant_id,
                            InventoryItemModel.id.in_(matched_ids),
                            InventoryItemModel.active.is_(True),
                        )
                    )
                )
            if found != matched_ids:
                raise V4AgentSafetyError("invalid_tenant_material_match")
        for action in staged.material_actions:
            for reference in action.source_evidence:
                self._reference_evidence(reference)
        return any(action.action != "MATCH_EXISTING" for action in staged.material_actions)

    def _assert_evidence_fresh(self, references: list[EvidenceReference]) -> None:
        unique = {(item.sheet, item.cell): item for item in references}
        by_sheet: dict[str, list[EvidenceReference]] = {}
        for reference in unique.values():
            by_sheet.setdefault(reference.sheet, []).append(reference)
        for sheet, sheet_references in by_sheet.items():
            expected_by_cell = {
                reference.cell: self._reference_evidence(reference)
                for reference in sheet_references
            }
            current_items = self._read_exact_cells_batched(
                sheet,
                [reference.cell for reference in sheet_references],
            )
            for current in current_items:
                expected = expected_by_cell[current.cell]
                if current.evidence_hash != expected.evidence_hash:
                    raise V4AgentSafetyError("stale_evidence")

    def _validate_generic_transform(self, operation) -> bool:
        """Mechanically verify a declared transform, never its business meaning.

        ``extract_component`` is deliberately generic: the prompt decides why a
        delimiter represents a dimension; the host only checks the requested
        split/select result against already-read source evidence.
        """
        transform = operation.transformation
        if not transform:
            # Legacy evidence-backed transforms retain their configured mode;
            # new declared transforms below are mechanically recomputed.
            return False
        kind = str(transform.get("type") or "")
        source_data = transform.get("source") or {}
        try:
            source = self._reference_evidence(EvidenceReference.model_validate(source_data))
        except ValidationError as exc:
            raise V4AgentSafetyError("missing_or_invalid_evidence") from exc
        if kind == "exact_copy":
            return operation.value != source.raw_value
        if kind == "extract_component":
            delimiter, index = str(transform.get("delimiter") or ""), transform.get("index")
            if not delimiter or not isinstance(index, int) or not isinstance(source.raw_value, str):
                return True
            parts = source.raw_value.split(delimiter)
            if index < 0 or index >= len(parts):
                return True
            expected = parts[index].strip()
            if bool(transform.get("normalize_numeric")):
                try:
                    expected = str(Decimal(expected.replace(",", ".")))
                    actual = str(Decimal(str(operation.value).replace(",", ".")))
                except (InvalidOperation, ValueError):
                    return True
                return actual != expected
            return str(operation.value).strip() != expected
        # Catalog conversions require a separate evidence-backed conversion
        # registry integration; unsupported transforms are review-only.
        return True

    def stage_edits(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if self.staged is not None:
            raise V4AgentSafetyError("stage_edits_already_called")
        if self.assessment is None:
            raise V4AgentSafetyError("assessment_required")
        if self.assessment.additional_reads_needed:
            raise V4AgentSafetyError("assessment_incomplete")
        staged = StagedEdits.model_validate(dict(arguments))
        if (
            staged.status == "ready"
            and not staged.operations
            and not staged.issues
            and not staged.material_actions
            and not self.assessment.observations
        ):
            raise V4AgentSafetyError("grounded_assessment_required")
        if len(staged.operations) > self.max_edit_operations:
            raise V4AgentLimitExceeded("edit_operation_limit_exceeded")
        targets: set[tuple[str, str]] = set()
        references: list[EvidenceReference] = list(self.assessment_references)
        transformed = staged.requires_review or staged.status == "review_required"
        set_operations = []
        clear_operations = []
        for operation in staged.operations:
            self._authorize_sheet(operation.sheet)
            target = (operation.sheet, operation.cell)
            if target in targets:
                raise V4AgentSafetyError("duplicate_or_conflicting_target")
            targets.add(target)
            if not self._target_within_grid(*target):
                raise V4AgentSafetyError("target_out_of_grid")
            target_evidence = self.ledger.get(target)
            if target_evidence is None:
                raise V4AgentSafetyError("target_not_read")
            if target_evidence.formula is not None or self._is_restricted_target(*target):
                raise V4AgentSafetyError("formula_or_restricted_target")
            references.extend(operation.evidence)
            for reference in operation.evidence:
                self._reference_evidence(reference)
            if operation.copy_from:
                source = self._reference_evidence(operation.copy_from)
                references.append(operation.copy_from)
                if operation.type == "set_cell" and operation.provenance == "exact_copy" and operation.value != source.raw_value:
                    if source.raw_value in (None, "") and operation.value in (0, "0"):
                        raise V4AgentSafetyError("blank_is_not_zero")
                    raise V4AgentSafetyError("exact_copy_value_mismatch")
            if operation.provenance == "transformed" and self._validate_generic_transform(operation):
                transformed = True
            if operation.requires_review:
                transformed = True
            elif operation.provenance == "transformed" and not self.allow_auto_transforms:
                transformed = True
            (set_operations if operation.type == "set_cell" else clear_operations).append(operation)
        for issue in staged.issues:
            references.extend(issue.evidence)
            for reference in issue.evidence:
                self._reference_evidence(reference)
            transformed = transformed or issue.requires_review
        transformed = transformed or self._validate_material_actions(staged)
        self._assert_evidence_fresh(references)
        if transformed:
            staged.requires_review = True
            if staged.status == "ready":
                staged.status = "review_required"
        staged.operations = set_operations + clear_operations
        self.staged = staged
        return {
            "accepted": True,
            "apply_mode": "auto" if self.allow_auto_transforms else "shadow",
            "status": staged.status,
            "requires_review": staged.requires_review,
            "operation_count": len(staged.operations),
            "writes": 0,
        }

    def apply_staged(self) -> dict[str, Any]:
        """Apply only a ready, revalidated V4 plan; sets verify before clears."""
        if self.staged is None:
            raise V4AgentSafetyError("staged_edits_required")
        if self.staged.status != "ready" or self.staged.requires_review:
            self.last_execution = {
                "status": self.staged.status,
                "writes": 0,
                "set_count": 0,
                "clear_count": 0,
                "verification_status": "not_executed",
            }
            return self.last_execution
        references = list(self.assessment_references)
        for operation in self.staged.operations:
            references.extend(operation.evidence)
            if operation.copy_from is not None:
                references.append(operation.copy_from)
        self._assert_evidence_fresh(references)

        def target(operation) -> str:
            return f"'{operation.sheet.replace(chr(39), chr(39) * 2)}'!{operation.cell}"

        set_operations = [item for item in self.staged.operations if item.type == "set_cell"]
        clear_operations = [item for item in self.staged.operations if item.type == "clear_cell"]
        set_ranges = [target(item) for item in set_operations]
        if set_operations:
            self.google.batch_update_values(
                self.spreadsheet_file_id,
                [{"range": item, "majorDimension": "ROWS", "values": [[operation.value]]}
                 for item, operation in zip(set_ranges, set_operations, strict=True)],
            )
            observed = self.google.batch_get_values(self.spreadsheet_file_id, set_ranges)
            for expected, block in zip(set_operations, observed, strict=True):
                values = block.get("values") or []
                actual = values[0][0] if values and values[0] else None
                if str(actual).strip() != str(expected.value).strip():
                    raise V4AgentSafetyError("set_readback_verification_failed")

        clear_ranges = [target(item) for item in clear_operations]
        if clear_ranges:
            self.google.batch_clear_values(self.spreadsheet_file_id, clear_ranges)
            observed = self.google.batch_get_values(self.spreadsheet_file_id, clear_ranges)
            if any((block.get("values") or []) for block in observed):
                raise V4AgentSafetyError("clear_readback_verification_failed")
        self.last_execution = {
            "status": "completed",
            "writes": len(set_operations) + len(clear_operations),
            "set_count": len(set_operations),
            "clear_count": len(clear_operations),
            "verification_status": "verified",
        }
        return self.last_execution

    def execute(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        handlers = {
            "get_workbook_metadata": self.get_workbook_metadata,
            "read_range": self.read_range,
            "read_cells": self.read_cells,
            "get_material_catalog": self.get_material_catalog,
            "search_material_catalog": self.search_material_catalog,
            "submit_workbook_assessment": self.submit_workbook_assessment,
            "propose_knowledge": self.propose_knowledge,
            "stage_edits": self.stage_edits,
        }
        handler = handlers.get(name)
        if handler is None:
            raise V4AgentSafetyError("unknown_tool")
        try:
            result = handler(arguments)
        except V4AgentLimitExceeded:
            raise
        except V4AgentSafetyError as exc:
            error_code = str(exc)
            if error_code not in _CORRECTABLE_TOOL_ERRORS:
                raise
            result = {
                "accepted": False,
                "error": error_code,
                "instruction": "Correct the tool arguments or gather valid evidence, then retry.",
            }
            self.tool_trace.append({"tool": name, "accepted": False, "error": error_code})
            return result
        except ValidationError:
            result = {
                "accepted": False,
                "error": "invalid_tool_arguments",
                "instruction": "Correct the tool arguments to match the declared schema, then retry.",
            }
            self.tool_trace.append(
                {"tool": name, "accepted": False, "error": "invalid_tool_arguments"}
            )
            return result
        trace: dict[str, Any] = {"tool": name}
        if name == "read_range":
            trace.update(
                {
                    "sheet": result["sheet"],
                    "range": result["range"],
                    "cells": len(result["cells"]),
                }
            )
        elif name == "read_cells":
            trace.update(
                {
                    "sheet": result["sheet"],
                    "cells": len(result["cells"]),
                }
            )
        elif name in {"get_material_catalog", "search_material_catalog"}:
            trace["count"] = len(result["materials"])
        elif name == "submit_workbook_assessment":
            trace.update(
                {
                    "observations": result["grounded_observations"],
                    "uncertainties": result["uncertainties"],
                    "additional_reads_needed": result["additional_reads_needed"],
                }
            )
        elif name == "propose_knowledge":
            trace["proposal_count"] = result["proposal_count"]
        elif name == "stage_edits":
            trace["operation_count"] = result["operation_count"]
        self.tool_trace.append(trace)
        return result


def function_declarations() -> list[dict[str, Any]]:
    reference = EvidenceReference.model_json_schema()
    return [
        {
            "name": "get_workbook_metadata",
            "description": "Return authorized workbook and sheet metadata, including grids, merges and protected ranges.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "read_range",
            "description": "Read an arbitrary authorized A1 range as exact cell-addressed evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet": {"type": "string"},
                    "a1_range": {"type": "string"},
                    "include_formulas": {"type": "boolean"},
                },
                "required": ["sheet", "a1_range"],
            },
        },
        {
            "name": "read_cells",
            "description": "Read a bounded list of exact cells from one authorized sheet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet": {"type": "string"},
                    "cells": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["sheet", "cells"],
            },
        },
        {
            "name": "search_material_catalog",
            "description": "Search the active tenant material catalog by SKU, name, alias, or category. Results are bounded; prefer a specific query or category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "category": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
            },
        },
        {
            "name": "submit_workbook_assessment",
            "description": "Submit a grounded workbook assessment before staging. Cite only evidence returned by read tools.",
            "parametersJsonSchema": WorkbookAssessment.model_json_schema(),
        },
        {
            "name": "propose_knowledge",
            "description": "Propose a reusable workbook rule for human review. The proposal must cite already-read evidence and is never activated automatically.",
            "parametersJsonSchema": KnowledgeProposal.model_json_schema(),
        },
        {
            "name": "stage_edits",
            "description": "Submit the authoritative evidence-backed shadow edit plan after assessment. This never writes Google Sheets.",
            "parametersJsonSchema": StagedEdits.model_json_schema(),
        },
    ]
