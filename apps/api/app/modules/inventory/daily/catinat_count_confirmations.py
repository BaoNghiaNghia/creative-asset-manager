"""Staff-confirmed Catinat physical counts, append-only and source-bound.

Does NOT sign/close a shift, approve exceptions, mutate Google Sheets, or
authorize carry-forward. A source hash comparison is NOT atomic CAS: a later
fresh source read is mandatory before any subsequent official close.
"""
from __future__ import annotations

from datetime import date
import re
from typing import Callable, Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.persistence_model import (
    InventoryCatinatCountConfirmationModel, inventory_utcnow,
)

_HASH = re.compile(r"^[a-f0-9]{64}$")
_CELL = re.compile(r"^[A-Z]{1,2}[1-9][0-9]{0,3}$")
_SHEETS = frozenset(("KHO PHA CHẾ", "PHÒNG PHA CHẾ", "KHO PHÒNG RANG"))


class CatinatCountBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _key(cell: dict) -> tuple[str, str]:
    sheet = cell.get("sheet", "")
    cell_ref = cell.get("cell", "")
    if sheet not in _SHEETS or not isinstance(cell_ref, str) or not _CELL.fullmatch(cell_ref):
        raise CatinatCountBlocked("invalid_physical_cell_reference")
    return sheet, cell_ref


def _source(ready: dict, business_date: date) -> tuple[str, str]:
    if ready.get("business_date") != business_date.isoformat() or ready.get("shift") != "evening":
        raise CatinatCountBlocked("source_shift_date_unverifiable")
    if ready.get("mode") != "read_only_not_a_signed_close":
        raise CatinatCountBlocked("untrusted_readiness_source")
    digest, spreadsheet = ready.get("source_hash"), ready.get("spreadsheet_id")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest) or not isinstance(spreadsheet, str) or not spreadsheet:
        raise CatinatCountBlocked("unverified_google_sheet_source")
    if not isinstance(ready.get("items"), list):
        raise CatinatCountBlocked("physical_roster_unavailable")
    return digest, spreadsheet


def _entry(row: InventoryCatinatCountConfirmationModel, *, replay: bool) -> dict:
    return {
        "id": row.id,
        "business_date": row.business_date.isoformat(),
        "shift": row.shift,
        "sheet": row.sheet_name,
        "cell": row.cell_ref,
        "material": row.material_display,
        "form": row.form,
        "unit": row.unit,
        "raw": row.physical_raw,
        "quantity": row.physical_quantity,
        "source_hash": row.source_hash,
        "confirmed_by": row.confirmed_by,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "idempotent_replay": replay,
    }


class CatinatCountConfirmationService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def confirm(
        self, *, tenant_id: str, actor_id: str, business_date: date,
        expected_source_hash: str, cells: list[dict],
        current_readiness: Callable[[], dict],
    ) -> dict:
        if not str(tenant_id).strip() or not str(actor_id).strip():
            raise CatinatCountBlocked("count_confirmation_actor_required")
        if not isinstance(expected_source_hash, str) or not _HASH.fullmatch(expected_source_hash):
            raise CatinatCountBlocked("invalid_source_fingerprint")
        if not isinstance(cells, list) or not 1 <= len(cells) <= 50:
            raise CatinatCountBlocked("invalid_confirmation_batch_size")
        selected = [_key(item) for item in cells]
        if len(set(selected)) != len(selected):
            raise CatinatCountBlocked("duplicate_physical_cells")
        ready = current_readiness()
        digest, spreadsheet = _source(ready, business_date)
        if digest != expected_source_hash:
            raise CatinatCountBlocked("google_sheet_changed_reconfirm_required")
        authoritative = {}
        for item in ready["items"]:
            key = _key(item)
            if key in authoritative:
                raise CatinatCountBlocked("duplicate_source_count_identity")
            authoritative[key] = item
        rows = []
        for key in selected:
            observed = authoritative.get(key)
            if observed is None:
                raise CatinatCountBlocked("physical_cell_not_in_source_roster")
            if (
                observed.get("quantity") is None
                or observed.get("status") != "physical_value_present_unconfirmed"
                or not observed.get("unit")
                or not observed.get("material")
            ):
                raise CatinatCountBlocked("physical_value_missing_or_ambiguous")
            rows.append(observed)
        output: list[dict] = []
        with self.sessions() as session:
            # Stable locks + unique DB constraint prevent simultaneous
            # duplicate requests. SQLite tests rely on the unique constraint.
            try:
                with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                            {"key": f"catinat-count:{tenant_id}:{business_date}"},
                        )
                    for observed in rows:
                        sheet, cell_ref = _key(observed)
                        existing = session.scalar(select(InventoryCatinatCountConfirmationModel).where(
                            InventoryCatinatCountConfirmationModel.tenant_id == tenant_id,
                            InventoryCatinatCountConfirmationModel.business_date == business_date,
                            InventoryCatinatCountConfirmationModel.shift == "evening",
                            InventoryCatinatCountConfirmationModel.sheet_name == sheet,
                            InventoryCatinatCountConfirmationModel.cell_ref == cell_ref,
                            InventoryCatinatCountConfirmationModel.source_hash == digest,
                        ))
                        if existing is not None:
                            if existing.confirmed_by != actor_id:
                                raise CatinatCountBlocked("physical_cell_already_confirmed_by_other")
                            output.append(_entry(existing, replay=True))
                            continue
                        record = InventoryCatinatCountConfirmationModel(
                            tenant_id=tenant_id, business_date=business_date,
                            shift="evening", spreadsheet_file_id=spreadsheet,
                            sheet_name=sheet, cell_ref=cell_ref, source_hash=digest,
                            material_display=str(observed["material"]),
                            form=str(observed["form"]), unit=str(observed["unit"]),
                            physical_quantity=str(observed["quantity"]),
                            physical_raw=str(observed["raw"]),
                            confirmed_by=actor_id, confirmed_at=inventory_utcnow(),
                        )
                        session.add(record)
                        session.flush()
                        output.append(_entry(record, replay=False))
            except IntegrityError as exc:
                raise CatinatCountBlocked("concurrent_physical_confirmation_conflict") from exc
        return {
            "business_date": business_date.isoformat(),
            "source_hash": digest,
            "confirmed": output,
            "official_shift_closed": False,
            "inventory_write_performed": False,
            "sheets_writes": 0,
        }

    def state(
        self, *, tenant_id: str, business_date: date,
        current_readiness: Callable[[], dict],
    ) -> dict:
        ready = current_readiness()
        digest, spreadsheet = _source(ready, business_date)
        with self.sessions() as session:
            records = session.scalars(select(InventoryCatinatCountConfirmationModel).where(
                InventoryCatinatCountConfirmationModel.tenant_id == tenant_id,
                InventoryCatinatCountConfirmationModel.business_date == business_date,
                InventoryCatinatCountConfirmationModel.shift == "evening",
                InventoryCatinatCountConfirmationModel.spreadsheet_file_id == spreadsheet,
                InventoryCatinatCountConfirmationModel.source_hash == digest,
            )).all()
        evidence = {(row.sheet_name, row.cell_ref): row for row in records}
        current = []
        for item in ready["items"]:
            marker = (item["sheet"], item["cell"])
            recorded = evidence.get(marker)
            matches = bool(
                recorded
                and recorded.physical_quantity == item.get("quantity")
                and recorded.material_display == item.get("material")
                and recorded.unit == item.get("unit")
                and recorded.physical_raw == item.get("raw")
            )
            current.append({
                "sheet": marker[0], "cell": marker[1],
                "material": item.get("material"), "unit": item.get("unit"),
                "quantity": item.get("quantity"),
                "confirmed": matches,
                "confirmed_by": recorded.confirmed_by if matches else None,
            })
        return {
            "business_date": business_date.isoformat(),
            "source_hash": digest,
            "source_status": "unverified_while_sheet_can_change",
            "confirmed_count": sum(1 for item in current if item["confirmed"]),
            "required_count": len(current),
            "cells": current,
            "official_shift_closed": False,
            "automatic_carry_forward_allowed": False,
            "sheets_writes": 0,
        }
