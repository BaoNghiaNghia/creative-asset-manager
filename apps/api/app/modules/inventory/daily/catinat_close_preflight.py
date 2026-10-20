"""Q16/Q22/Q28/Q32/Q34/Q39: source-bound official-close safety preflight.

Combines authenticated per-cell confirmations with pending transfers and
manager decisions; NOT a signature and never authorizes Sheets writes.

Conservative until the item/location catalog mapping, signed AM handoff,
batch expiry/waste and safe concurrent carry-forward are fully integrated.
"""
from __future__ import annotations

from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, _source,
)
from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalRequestModel,
    InventoryCatinatCountConfirmationModel,
    InventoryCatinatSignedShiftModel,
    InventoryCatinatTransferModel,
)


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
        for cell in rows:
            key = cell["sheet"], cell["cell"]
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
            if valid:
                confirmed_count += 1
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
        # The following gates remain mandatory and are NOT satisfied by
        # simple name matching or by a manager 'approve' click alone.
        blockers.extend([
            {"code": "catalog_item_and_location_mapping_not_certified"},
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
            "blockers": blockers,
            "ready_to_sign": False,
            "official_shift_closed": signed is not None,
            "automatic_carry_forward_allowed": False,
            "sheets_writes": 0,
            "mode": "read_only_safety_preflight",
        }
