"""Q16/Q22/Q28/Q32/Q34/Q39: source-bound official-close safety preflight.

Combines authenticated per-cell confirmations with pending transfers and
manager decisions; NOT a signature and never authorizes Sheets writes.

The authoritative physical warehouse identity comes from Q01–Q47's three
configured workbook layouts, NOT from a count of registry items or locations.
Signed handoff, expiry/waste and safe concurrent carry-forward remain gated.
"""
from __future__ import annotations

from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, _source,
)
from app.modules.inventory.daily_sheet.catinat_shift_readiness import WAREHOUSES
from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalRequestModel,
    InventoryCatinatCountConfirmationModel,
    InventoryCatinatSignedShiftModel,
    InventoryCatinatTransferModel,
)


_CLOSING_CELLS = {
    "KHO PHA CHẾ": {"whole": "L", "loose": "M"},
    "PHÒNG PHA CHẾ": {"whole": "H", "loose": "J"},
    "KHO PHÒNG RANG": {"whole": "J", "loose": "L"},
}
assert set(_CLOSING_CELLS) == set(WAREHOUSES)


class CatinatClosePreflightService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def inspect(
        self, *, tenant_id: str, business_date: date, current_readiness,
    ) -> dict:
        ready = current_readiness()
        digest, workbook = _source(ready, business_date)
        blockers: list[dict] = []
        rows = ready["items"]
        identities: set[tuple[str, str]] = set()
        for cell in rows:
            identity = cell["sheet"], cell["cell"]
            if identity in identities:
                blockers.append({"code": "duplicate_source_count_identity", "cell": identity})
            identities.add(identity)
        with self.sessions() as session:
            confirmed = session.scalars(select(InventoryCatinatCountConfirmationModel).where(
                InventoryCatinatCountConfirmationModel.tenant_id == tenant_id,
                InventoryCatinatCountConfirmationModel.business_date == business_date,
                InventoryCatinatCountConfirmationModel.shift == "evening",
                InventoryCatinatCountConfirmationModel.spreadsheet_file_id == workbook,
                InventoryCatinatCountConfirmationModel.source_hash == digest,
            )).all()
            requests = session.scalars(select(InventoryCatinatApprovalRequestModel).where(
                InventoryCatinatApprovalRequestModel.tenant_id == tenant_id,
                InventoryCatinatApprovalRequestModel.business_date == business_date,
            )).all()
            transfers = session.scalars(select(InventoryCatinatTransferModel).where(
                InventoryCatinatTransferModel.tenant_id == tenant_id,
                InventoryCatinatTransferModel.business_date <= business_date,
            )).all()
            signed = session.scalar(select(InventoryCatinatSignedShiftModel).where(
                InventoryCatinatSignedShiftModel.tenant_id == tenant_id,
                InventoryCatinatSignedShiftModel.business_date == business_date,
                InventoryCatinatSignedShiftModel.shift == "evening",
            ))
        evidence = {(e.sheet_name, e.cell_ref): e for e in confirmed}
        confirmed_count = 0
        areas = {sheet: {"sheet": sheet, "whole_column": columns["whole"],
                         "loose_column": columns["loose"], "required_cells": 0,
                         "confirmed_cells": 0} for sheet, columns in _CLOSING_CELLS.items()}
        for cell in rows:
            key = cell["sheet"], cell["cell"]
            expected_column = _CLOSING_CELLS.get(key[0], {}).get(cell.get("form"))
            valid_source_identity = bool(
                expected_column and isinstance(key[1], str)
                and len(key[1]) > 1 and key[1].startswith(expected_column)
                and key[1][len(expected_column):].isdigit()
                and int(key[1][len(expected_column):]) >= 5
                and bool(cell.get("material")) and bool(cell.get("unit"))
            )
            if not valid_source_identity:
                blockers.append({"code": "invalid_q47_warehouse_cell_identity",
                                 "sheet": key[0], "cell": key[1]})
            else:
                areas[key[0]]["required_cells"] += 1
            evidence_row = evidence.get(key)
            valid = (
                evidence_row is not None
                and evidence_row.material_display == cell.get("material")
                and evidence_row.unit == cell.get("unit")
                and evidence_row.physical_raw == cell.get("raw")
                and evidence_row.physical_quantity == cell.get("quantity")
                and evidence_row.form == cell.get("form")
                and bool(evidence_row.confirmed_by)
            )
            if valid and valid_source_identity:
                confirmed_count += 1
                areas[key[0]]["confirmed_cells"] += 1
            else:
                blockers.append({
                    "code": "physical_count_unconfirmed_or_stale",
                    "sheet": key[0], "cell": key[1],
                })
        if not rows:
            blockers.append({"code": "no_physical_counts_discovered"})
        for issue in ready.get("blockers", []):
            # Employee confirmation is tracked independently in the ledger.
            if issue.get("code") == "employee_physical_confirmation_required":
                continue
            blockers.append(dict(issue))
        for request in requests:
            code = (
                "manager_exception_pending" if request.status == "pending"
                else "approved_exception_not_yet_applied" if request.status == "approved"
                else "rejected_exception_requires_reconciliation"
            )
            blockers.append({
                "code": code, "request_id": request.id,
                "kind": request.kind, "subject_ref": request.subject_ref,
            })
        pending = []
        unreconciled = []
        for transfer in transfers:
            if transfer.status != "received":
                pending.append(transfer.id)
            if not transfer.ledger_posted:
                unreconciled.append(transfer.id)
        if pending:
            blockers.append({
                "code": "pending_transfers_require_receipt_or_manager_exception",
                "transfer_ids": sorted(pending),
            })
        if unreconciled:
            blockers.append({
                "code": "transfer_ledger_reconciliation_required",
                "transfer_ids": sorted(unreconciled),
            })
        if signed is not None:
            blockers.append({
                "code": ("shift_already_signed" if signed.source_hash == digest
                         else "post_close_sheet_changed_manager_review_required"),
                "snapshot_id": signed.id,
            })
        # Q01-Q47 treats the exact workbook tab/closing-cell/unit as the
        # operational stock identity. Registry row counts must NEVER gate a
        # physical count or create synthetic warehouse locations. Actual
        # posting/closing remains blocked by independent safety controls.
        blockers.extend([
            {"code": "batch_expiration_and_waste_reconciliation_not_certified"},
            {"code": "shift_reconciliation_and_transfer_posting_not_certified"},
            {"code": "concurrent_google_sheet_write_protocol_not_certified"},
        ])
        return {
            "business_date": business_date.isoformat(),
            "shift": "evening",
            "source_hash": digest,
            "confirmed_count": confirmed_count,
            "required_count": len(rows),
            "outstanding_transfer_count": len(pending),
            "unreconciled_transfer_count": len(unreconciled),
            "approval_requests_count": len(requests),
            "warehouse_sources": list(areas.values()),
            "identity_source": "q01_q47_configured_worksheet_and_closing_cell",
            "physical_identity_verified_for_preview": bool(rows) and not any(
                item["code"] in {"invalid_q47_warehouse_cell_identity",
                                 "duplicate_source_count_identity"} for item in blockers
            ),
            "blockers": blockers,
            "ready_to_sign": False,
            "official_shift_closed": signed is not None,
            "automatic_carry_forward_allowed": False,
            "sheets_writes": 0,
            "mode": "read_only_safety_preflight",
        }
