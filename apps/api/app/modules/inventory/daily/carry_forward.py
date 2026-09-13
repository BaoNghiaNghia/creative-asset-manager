"""Trusted 09:00 shared-workbook carry-forward.

Gemini may plan from the previous verified daily copy, but only this module can
write the shared workbook.  It deliberately accepts an injected planner so
tests and provider adapters share the exact same server-side validation path.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily_sheet.google_client import GoogleSheetsInventoryClient
from app.modules.inventory.daily_sheet.parser import canonical_hash
from app.modules.inventory.persistence_model import (
    InventoryDailyCarryForwardModel,
    InventoryDailySheetSnapshotModel,
    InventoryItemModel,
    InventoryLocationModel,
    InventorySettingsModel,
    inventory_utcnow,
)
from app.providers.google.auth import get_connection_access_token
from app.modules.assets.model import ExternalSourceModel
from app.modules.inventory.daily_sheet.prompts import InventoryPromptResolver


CARRY_FORWARD_PROMPT = """You are planning a narrow previous-terminal-state to current-starting-state carry-forward. You have two authorized workbooks: SOURCE is the previous verified Gemini workbook and TARGET is the current shared operational workbook. Do not write either workbook. Understand workbook roles, layouts, dimensions, and quantity representations from metadata and exact cell evidence; do not assume sheet positions, columns, rows, or labels have fixed meanings. Resolve canonical material and location identities through the tenant catalogs. Preserve every workbook-defined dimension independently and never total, redistribute, or invent conversions unless evidence and explicit business instructions justify it. Blank is not zero. Do not change formulas, labels, dates, units, notes, or unrelated fields. Every row must cite exact source and target evidence and structured semantic context. Report ambiguity as an issue. Finish by calling submit_carry_forward_plan exactly once. The backend controls authorization and write safety; you control workbook interpretation."""


class CarryForwardError(RuntimeError):
    code = "carry_forward_blocked"


class CarryForwardReviewRequired(CarryForwardError):
    code = "review_required"


class CarryForwardStaleEvidence(CarryForwardError):
    code = "stale_evidence"


@dataclass(frozen=True)
class CarryForwardPlan:
    rows: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    # Retained only for in-process adapter compatibility. It is neither
    # persisted nor interpreted as a sheet-role decision.
    legacy_metadata: dict[str, Any] | None = None
    contract_version: int = 3


class CarryForwardPlanner(Protocol):
    def plan(
        self, *, tenant_id: str, previous_gemini_file_id: str,
        shared_workbook_id: str, prompt: str, connection_id: str = "",
    ) -> CarryForwardPlan: ...


class UnavailableCarryForwardPlanner:
    """Fail closed until a Gemini tool adapter is configured for the tenant."""

    def plan(self, **_kwargs: Any) -> CarryForwardPlan:
        raise CarryForwardReviewRequired("carry_forward_planner_unavailable")


def _a1_parts(cell: str) -> tuple[int, int]:
    letters = ""
    digits = ""
    for value in str(cell).upper():
        if "A" <= value <= "Z" and not digits:
            letters += value
        elif value.isdigit():
            digits += value
        else:
            raise CarryForwardReviewRequired("invalid_target_cell")
    if not letters or not digits or int(digits) < 1:
        raise CarryForwardReviewRequired("invalid_target_cell")
    column = 0
    for value in letters:
        column = column * 26 + ord(value) - ord("A") + 1
    return int(digits) - 1, column - 1


def _range_contains(cell: str, range_data: dict[str, Any]) -> bool:
    row, column = _a1_parts(cell)
    start_row = int(range_data.get("startRowIndex") or 0)
    end_row = int(range_data.get("endRowIndex") or 1 << 30)
    start_column = int(range_data.get("startColumnIndex") or 0)
    end_column = int(range_data.get("endColumnIndex") or 1 << 30)
    return start_row <= row < end_row and start_column <= column < end_column


def _number(value: Any) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise CarryForwardReviewRequired("missing_closing_evidence")
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise CarryForwardReviewRequired("non_numeric_closing_evidence") from exc


class InventorySharedCarryForwardService:
    def __init__(
        self, session_factory: sessionmaker[Session], *,
        client_factory=GoogleSheetsInventoryClient,
        token_resolver=get_connection_access_token,
        planner: CarryForwardPlanner | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.client_factory = client_factory
        self.token_resolver = token_resolver
        if planner is None:
            # Import here to avoid the production builder importing this module at
            # module-load time. Explicit injection remains available for tests.
            from app.modules.inventory.daily.carry_forward_planner import build_carry_forward_planner
            planner = build_carry_forward_planner(session_factory=session_factory, client_factory=client_factory, token_resolver=token_resolver)
        self.planner = planner

    def _token(self, connection_id: str) -> str:
        value = self.token_resolver(connection_id)
        return asyncio.run(value) if hasattr(value, "__await__") else str(value)

    def _context(self, tenant_id: str, target_business_date: date):
        previous_business_date = date.fromordinal(target_business_date.toordinal() - 1)
        with self.session_factory() as session:
            settings = session.scalar(select(InventorySettingsModel).where(
                InventorySettingsModel.tenant_id == tenant_id,
                InventorySettingsModel.enabled.is_(True),
                InventorySettingsModel.daily_sheet_automation_enabled.is_(True),
            ))
            source = session.scalar(select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.id == (settings.external_source_id if settings else ""),
            ))
            snapshot = session.scalar(select(InventoryDailySheetSnapshotModel).where(
                InventoryDailySheetSnapshotModel.tenant_id == tenant_id,
                InventoryDailySheetSnapshotModel.business_date == previous_business_date,
                InventoryDailySheetSnapshotModel.status == "completed",
                InventoryDailySheetSnapshotModel.gemini_reconcile_status == "completed",
                InventoryDailySheetSnapshotModel.gemini_reconcile_verified_at.is_not(None),
            ))
            if not settings or not source or not settings.daily_working_spreadsheet_file_id:
                raise CarryForwardError("carry_forward_configuration_incomplete")
            if not snapshot or not snapshot.gemini_file_id:
                raise CarryForwardError("previous_day_gemini_not_verified")
            connection_id = str((source.source_metadata or {}).get("oauth_connection_id") or "")
            if not connection_id:
                raise CarryForwardError("carry_forward_google_connection_missing")
            return (
                str(settings.daily_working_spreadsheet_file_id), str(snapshot.gemini_file_id),
                str(snapshot.id), connection_id, previous_business_date,
            )

    def _operation(self, tenant_id: str, target_business_date: date, previous_business_date: date) -> InventoryDailyCarryForwardModel:
        with self.session_factory() as session:
            row = session.scalar(select(InventoryDailyCarryForwardModel).where(
                InventoryDailyCarryForwardModel.tenant_id == tenant_id,
                InventoryDailyCarryForwardModel.target_business_date == target_business_date,
            ))
            if row is None:
                row = InventoryDailyCarryForwardModel(
                    tenant_id=tenant_id, target_business_date=target_business_date,
                    previous_business_date=previous_business_date, status="pending",
                    idempotency_key=(
                        f"inventory-shared-carry-forward:v1:{tenant_id}:"
                        f"{target_business_date.isoformat()}"
                    ),
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row

    @staticmethod
    def _cell_value(google: Any, file_id: str, sheet: str, cell: str, *, formula: bool = False) -> Any:
        result = google.batch_get_values(
            file_id, [f"'{sheet}'!{cell}"],
            value_render_option="FORMULA" if formula else "UNFORMATTED_VALUE",
        )
        values = (result[0].get("values") or [[]])[0] if result else []
        return values[0] if values else None

    @staticmethod
    def _assert_writable_target(metadata: dict[str, Any], sheet: str, cell: str) -> None:
        target = next((item for item in metadata.get("sheets") or [] if (item.get("properties") or {}).get("title") == sheet), None)
        if target is None:
            raise CarryForwardReviewRequired("warehouse_sheet_changed")
        props = target.get("properties") or {}
        sheet_id = props.get("sheetId")
        for merge in target.get("merges") or []:
            if merge.get("sheetId") == sheet_id and _range_contains(cell, merge):
                raise CarryForwardReviewRequired("merged_target_cell")
        for protected in target.get("protectedRanges") or []:
            protected_range = protected.get("range") or {}
            if protected_range.get("sheetId") == sheet_id and _range_contains(cell, protected_range):
                raise CarryForwardReviewRequired("protected_target_cell")

    def _validate_plan(self, tenant_id: str, plan: CarryForwardPlan, *, source_id: str, target_id: str, source_meta: dict[str, Any], target_meta: dict[str, Any]) -> list[dict[str, Any]]:
        if plan.contract_version != 3:
            raise CarryForwardReviewRequired("carry_forward_plan_contract_outdated")
        target_sheets = {str((item.get("properties") or {}).get("title") or "") for item in target_meta.get("sheets") or []}
        if plan.issues:
            raise CarryForwardReviewRequired("carry_forward_plan_has_issues")
        validated: list[dict[str, Any]] = []
        material_ids: set[str] = set()
        warehouse_ids: set[str] = set()
        targets: set[tuple[str, str]] = set()
        with self.session_factory() as session:
            for row in plan.rows:
                operation_type = str(row.get("type") or "set_cell")
                source = row.get("source") or {}
                target = row.get("target") or {}
                if operation_type not in {"set_cell", "clear_cell"}:
                    raise CarryForwardReviewRequired("unsupported_carry_forward_operation")
                if target.get("spreadsheet_file_id") != target_id:
                    raise CarryForwardReviewRequired("cross_workbook_plan")
                target_cell = (str(target.get("sheet") or ""), str(target.get("cell") or "").upper())
                if not all(target_cell):
                    raise CarryForwardReviewRequired("invalid_target_cell")
                if target_cell in targets:
                    raise CarryForwardReviewRequired("duplicate_or_conflicting_target")
                targets.add(target_cell)
                if str(target.get("sheet") or "") not in target_sheets:
                    raise CarryForwardReviewRequired("referenced_sheet_not_found")
                self._assert_writable_target(target_meta, target_cell[0], target_cell[1])
                if not target.get("evidence_hash"):
                    raise CarryForwardReviewRequired("target_not_read")
                if operation_type == "clear_cell":
                    validated.append({**row, "type": operation_type, "target": {**target, "cell": target_cell[1]}})
                    continue
                material_id, warehouse_id = str(row.get("material_id") or ""), str(row.get("warehouse_id") or "")
                if source.get("spreadsheet_file_id") != source_id or target.get("spreadsheet_file_id") != target_id:
                    raise CarryForwardReviewRequired("cross_workbook_plan")
                if not session.scalar(select(InventoryItemModel.id).where(InventoryItemModel.tenant_id == tenant_id, InventoryItemModel.id == material_id, InventoryItemModel.active.is_(True))):
                    raise CarryForwardReviewRequired("unknown_material")
                if not session.scalar(select(InventoryLocationModel.id).where(InventoryLocationModel.tenant_id == tenant_id, InventoryLocationModel.id == warehouse_id, InventoryLocationModel.active.is_(True))):
                    raise CarryForwardReviewRequired("unknown_warehouse")
                if str(source.get("sheet") or "") not in {str((item.get("properties") or {}).get("title") or "") for item in source_meta.get("sheets") or []} or str(target.get("sheet") or "") not in target_sheets:
                    raise CarryForwardReviewRequired("referenced_sheet_not_found")
                source_value = source.get("closing_value")
                opening_value = target.get("opening_value")
                if _number(source_value) != _number(opening_value):
                    raise CarryForwardReviewRequired("closing_opening_mismatch")
                self._assert_writable_target(target_meta, str(target.get("sheet")), str(target.get("cell")))
                material_ids.add(material_id)
                warehouse_ids.add(warehouse_id)
                planned_value = row.get("value", target.get("opening_value"))
                if _number(source_value) != _number(planned_value):
                    raise CarryForwardReviewRequired("closing_opening_mismatch")
                validated.append({**row, "type": operation_type, "value": planned_value, "target": {**target, "cell": target_cell[1]}})
        if not validated:
            raise CarryForwardReviewRequired("empty_carry_forward_plan")
        return validated

    def run(self, tenant_id: str, target_business_date: date) -> InventoryDailyCarryForwardModel:
        shared_id, source_id, snapshot_id, connection_id, previous_date = self._context(tenant_id, target_business_date)
        operation = self._operation(tenant_id, target_business_date, previous_date)
        if operation.status == "completed":
            return operation
        try:
            with self.client_factory(self._token(connection_id)) as google:
                google.validate_native_spreadsheet(source_id)
                source_meta = google.spreadsheet_metadata(source_id)
                target_drive = google.validate_native_spreadsheet(shared_id)
                if (target_drive.get("capabilities") or {}).get("canEdit") is False:
                    raise CarryForwardError("shared_workbook_not_editable")
                target_meta = google.spreadsheet_metadata(shared_id)
                persisted = operation.plan_json if isinstance(operation.plan_json, dict) else None
                if persisted and operation.status in {"applying", "verifying", "retryable_failure"}:
                    if persisted.get("contract_version") != 3:
                        raise CarryForwardReviewRequired("carry_forward_plan_contract_outdated")
                    plan = CarryForwardPlan(list(persisted.get("operations") or []), list(persisted.get("issues") or []), None, 3)
                else:
                    with self.session_factory() as session:
                        row = session.get(InventoryDailyCarryForwardModel, operation.id)
                        resolved = InventoryPromptResolver(self.session_factory).freeze(row, tenant_id, "carry_forward_0900", prefix="prompt")
                        session.commit()
                    plan = self.planner.plan(
                        tenant_id=tenant_id, previous_gemini_file_id=source_id,
                        shared_workbook_id=shared_id, prompt=f"{CARRY_FORWARD_PROMPT}\n\n=== TENANT BUSINESS INSTRUCTIONS ===\n{resolved.content}\n=== END TENANT BUSINESS INSTRUCTIONS ===\n\nThe server binds the two workbook roles; never request identifiers.", connection_id=connection_id,
                    )
                rows = self._validate_plan(tenant_id, plan, source_id=source_id, target_id=shared_id, source_meta=source_meta, target_meta=target_meta)
                plan_json = {"contract_version": 3, "operations": rows, "issues": plan.issues}
                plan_hash = hashlib.sha256(json.dumps(plan_json, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
                set_updates: list[dict[str, Any]] = []
                clear_ranges: list[str] = []
                # Validate every operation and its freshly-read evidence before
                # any external mutation.  This keeps reset fields intact when a
                # required opening set cannot safely be applied.
                for row in rows:
                    source, target = row.get("source") or {}, row["target"]
                    actual_target = self._cell_value(google, shared_id, target["sheet"], target["cell"])
                    formula = self._cell_value(google, shared_id, target["sheet"], target["cell"], formula=True)
                    if str(formula).startswith("="):
                        raise CarryForwardReviewRequired("formula_target_cell")
                    if canonical_hash([actual_target]) != target.get("evidence_hash"):
                        raise CarryForwardStaleEvidence("stale_evidence")
                    if row["type"] == "clear_cell":
                        if actual_target not in (None, ""):
                            clear_ranges.append(f"'{target['sheet']}'!{target['cell']}")
                        continue
                    actual_source = self._cell_value(google, source_id, source["sheet"], source["cell"])
                    if canonical_hash([actual_source]) != source.get("evidence_hash"):
                        raise CarryForwardStaleEvidence("stale_evidence")
                    if _number(actual_source) != _number(source["closing_value"]):
                        raise CarryForwardStaleEvidence("stale_evidence")
                    if _number(actual_source) != _number(row["value"]):
                        raise CarryForwardStaleEvidence("stale_evidence")
                    if _number(actual_target) != _number(row["value"]):
                        set_updates.append({"range": f"'{target['sheet']}'!{target['cell']}", "values": [[row["value"]]]})
                with self.session_factory() as session:
                    row = session.get(InventoryDailyCarryForwardModel, operation.id)
                    row.previous_snapshot_id, row.source_gemini_file_id, row.shared_target_file_id = snapshot_id, source_id, shared_id
                    row.warehouse_sheet_identity_json, row.plan_json, row.plan_hash = {"contract_version": 3}, plan_json, plan_hash
                    row.material_count = len({item.get("material_id") for item in rows if item.get("material_id")})
                    row.warehouse_count = len({item.get("warehouse_id") for item in rows if item.get("warehouse_id")})
                    row.issue_count, row.status, row.started_at = len(plan.issues), "applying", inventory_utcnow()
                    session.commit()
                if set_updates:
                    google.batch_update_values(shared_id, set_updates)
                for row in rows:
                    if row["type"] == "clear_cell":
                        continue
                    target = row["target"]
                    actual = self._cell_value(google, shared_id, target["sheet"], target["cell"])
                    if _number(actual) != _number(row["value"]):
                        raise CarryForwardStaleEvidence("read_back_mismatch")
                if clear_ranges:
                    google.batch_clear_values(shared_id, clear_ranges)
                for row in rows:
                    if row["type"] != "clear_cell":
                        continue
                    target = row["target"]
                    if self._cell_value(google, shared_id, target["sheet"], target["cell"]) not in (None, ""):
                        raise CarryForwardStaleEvidence("clear_read_back_mismatch")
                with self.session_factory() as session:
                    row = session.get(InventoryDailyCarryForwardModel, operation.id)
                    now = inventory_utcnow()
                    row.status, row.applied_at, row.verified_at, row.completed_at = "completed", now, now, now
                    row.error_code = row.error_message = None
                    session.commit(); session.refresh(row); session.expunge(row)
                    return row
        except CarryForwardReviewRequired as exc:
            status, code = "review_required", getattr(exc, "code", "review_required")
            error_message = str(exc)
        except Exception as exc:
            status, code = "retryable_failure", getattr(exc, "code", type(exc).__name__)
            error_message = str(exc)
        with self.session_factory() as session:
            row = session.get(InventoryDailyCarryForwardModel, operation.id)
            row.status, row.error_code, row.error_message = status, str(code)[:100], error_message[:1000]
            session.commit(); session.refresh(row); session.expunge(row)
            return row
