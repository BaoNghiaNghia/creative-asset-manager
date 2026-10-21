"""Fail-closed read-only Catinat shelf-life, waste and transfer triage (Q32/Q40).

Google Sheets stock cells have no trustworthy batch/opened/prepared timestamp or
unique disposal receipt. A date-only preparation expression is *not* 96-hour
evidence. Never mark stock safe, waste disposed, or transfer ledger reconciled.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_count_confirmations import (
    CatinatCountBlocked, _key, _source,
)
from app.modules.inventory.persistence_model import InventoryCatinatTransferModel
from app.modules.inventory.daily_sheet.catinat_closing import SHEET as REPORT_TAB

_PREPARED_CODE = "prepared_time_and_expiry_require_staff_verification"


def read_only_shelf_life_transfer_audit(
    sessions: sessionmaker[Session], *, tenant_id: str,
    business_date: date, current_readiness,
) -> dict:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise CatinatCountBlocked("tenant_required")
    ready = current_readiness()
    if not isinstance(ready, dict):
        raise CatinatCountBlocked("untrusted_readiness_source")
    source_hash, _workbook = _source(ready, business_date)
    blockers = ready.get("blockers")
    if not isinstance(blockers, list) or not all(isinstance(b, dict) for b in blockers):
        raise CatinatCountBlocked("unverifiable_shelf_life_blockers")
    keys = [_key(row) for row in ready["items"]]
    if len(set(keys)) != len(keys):
        raise CatinatCountBlocked("duplicate_source_count_identity")
    prepared = []
    for b in blockers:
        if b.get("code") != _PREPARED_CODE:
            continue
        sheet, row = b.get("sheet"), b.get("row")
        if sheet != REPORT_TAB:
            raise CatinatCountBlocked("prepared_report_identity_unverifiable")
        if not isinstance(row, int) or isinstance(row, bool) or row < 7:
            raise CatinatCountBlocked("prepared_report_row_invalid")
        prepared.append({"sheet": sheet, "row": row,
                         "status": "prepared_time_unverified_no_expiry_claim"})
    # Report-row details are hints only: they carry no reliable time-of-day.
    prepared.sort(key=lambda row: (row["sheet"], row["row"]))
    if len({(row["sheet"], row["row"]) for row in prepared}) != len(prepared):
        raise CatinatCountBlocked("duplicate_prepared_report_evidence")

    with sessions() as session:
        transfers = session.scalars(select(InventoryCatinatTransferModel).where(
            InventoryCatinatTransferModel.tenant_id == tenant_id,
            InventoryCatinatTransferModel.business_date <= business_date,
        ).order_by(InventoryCatinatTransferModel.business_date,
                   InventoryCatinatTransferModel.id)).all()
        result_transfers = []
        for t in transfers:
            remaining = t.dispatched_quantity - t.received_quantity
            if (not isinstance(remaining, Decimal) or not remaining.is_finite()
                    or remaining < 0 or t.status not in
                    {"pending_receipt", "partial_receipt", "received"}):
                raise CatinatCountBlocked("invalid_transfer_ledger_evidence")
            if (remaining == 0) != (t.status == "received"):
                raise CatinatCountBlocked("transfer_state_quantity_conflict")
            result_transfers.append({
                "id": t.id, "business_date": t.business_date.isoformat(),
                "item_id": t.item_id, "source_location_id": t.source_location_id,
                "destination_location_id": t.destination_location_id,
                "unit": t.unit, "dispatched_quantity": format(t.dispatched_quantity, "f"),
                "received_quantity": format(t.received_quantity, "f"),
                "remaining_in_transit": format(remaining, "f"),
                "receipt_status": t.status, "ledger_posted": bool(t.ledger_posted),
                "requires_receipt": remaining > 0,
                "requires_ledger_reconciliation": not t.ledger_posted,
                "stock_posted_by_this_audit": False,
            })

    # A Sheets row has no batch-id, explicit opening/preparation timestamp,
    # product policy or manager-confirmed disposal. A zero physical count is
    # not itself evidence that expired stock was disposed of.
    items = [{
        "sheet": sheet, "cell": cell, "material": row.get("material"),
        "form": row.get("form"), "unit": row.get("unit"),
        "batch_expiry_status": "batch_identity_and_exact_time_required",
        "disposal_status": "physical_disposal_evidence_not_verified",
    } for row, (sheet, cell) in zip(ready["items"], keys)]
    return {
        "mode": "read_only_shelf_life_waste_transfer_triage",
        "business_date": business_date.isoformat(),
        "source_hash": source_hash,
        "prepared_report_hints": prepared,
        "items": items,
        "transfers": result_transfers,
        "summary": {
            "physical_cells_without_batch_expiry_certification": len(items),
            "date_only_preparation_hints": len(prepared),
            "pending_receipt_transfers": sum(t["requires_receipt"] for t in result_transfers),
            "unposted_ledger_transfers": sum(
                t["requires_ledger_reconciliation"] for t in result_transfers
            ),
        },
        "batch_expiration_and_waste_certified": False,
        "transfer_ledger_reconciled": False,
        "ready_to_sign": False,
        "automatic_carry_forward_allowed": False,
        "inventory_writes": 0, "sheets_writes": 0,
    }
