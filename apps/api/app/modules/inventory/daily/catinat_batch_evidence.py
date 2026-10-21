"""Catinat append-only batch attestation and bounded disposal-review requests.

Neither manager approval nor staff attestation proves actual disposal, permits a
stock decrement, certifies expiry, or writes Sheets.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_approvals import CatinatApprovalService
from app.modules.inventory.daily.catinat_count_confirmations import _key, _source, CatinatCountBlocked
from app.modules.inventory.persistence_model import InventoryCatinatBatchEvidenceModel, inventory_utcnow

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_LOT = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


class CatinatBatchBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _note(value: str) -> str:
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 1000:
        raise CatinatBatchBlocked("batch_note_required")
    return value.strip()


def _time(value: datetime, *, required: bool) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CatinatBatchBlocked("exact_timezone_aware_timestamp_required")
    return value.astimezone(timezone.utc)


def _decimal(value: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?", value):
        raise CatinatBatchBlocked("invalid_disposal_proposed_quantity")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CatinatBatchBlocked("invalid_disposal_proposed_quantity") from exc
    if not result.is_finite() or result <= 0 or result > Decimal("999999999999"):
        raise CatinatBatchBlocked("invalid_disposal_proposed_quantity")
    return result


def _source_row(ready: dict, *, business_date: date, sheet: str, cell: str,
                expected_hash: str) -> tuple[str, str, dict]:
    try:
        digest, spreadsheet = _source(ready, business_date)
        rows = [row for row in ready["items"] if _key(row) == (sheet, cell)]
    except CatinatCountBlocked as exc:
        raise CatinatBatchBlocked(exc.code) from exc
    if digest != expected_hash:
        raise CatinatBatchBlocked("google_sheet_changed_reconfirm_required")
    if len(rows) != 1:
        raise CatinatBatchBlocked("source_cell_not_unique_or_missing")
    row = rows[0]
    if (not all(isinstance(row.get(k), str) and row[k].strip()
                for k in ("material", "unit", "form"))
            or row["form"] not in {"whole", "loose"}):
        raise CatinatBatchBlocked("unverified_batch_stock_identity")
    return digest, spreadsheet, row


def _public(row: InventoryCatinatBatchEvidenceModel, *, replay: bool = False,
            current_source_hash: str | None = None) -> dict:
    return {
        "id": row.id, "business_date": row.business_date.isoformat(),
        "sheet": row.sheet_name, "cell": row.cell_ref,
        "source_hash": row.source_hash, "material": row.material_display,
        "form": row.form, "unit": row.unit, "lot_reference": row.lot_reference,
        "event_kind": row.event_kind,
        "event_at": row.event_at.isoformat(),
        "package_expiry_at": row.package_expiry_at.isoformat() if row.package_expiry_at else None,
        "note": row.note, "attested_by": row.attested_by,
        "attested_at": row.attested_at.isoformat() if row.attested_at else None,
        "status": "staff_attested_unverified",
        "source_current": row.source_hash == current_source_hash if current_source_hash else None,
        "expiry_certified": False, "disposal_performed": False,
        "ready_to_sign": False, "inventory_writes": 0, "sheets_writes": 0,
        "idempotent_replay": replay,
    }


class CatinatBatchEvidenceService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def record(self, *, tenant_id: str, actor_id: str, idempotency_key: str,
               business_date: date, expected_source_hash: str, sheet: str, cell: str,
               lot_reference: str, event_kind: str, event_at: datetime,
               package_expiry_at: datetime | None, note: str,
               current_readiness: Callable[[], dict]) -> dict:
        if not tenant_id or not actor_id or not _KEY.fullmatch(str(idempotency_key or "")):
            raise CatinatBatchBlocked("batch_actor_or_idempotency_required")
        if event_kind not in {"prepared", "opened"}:
            raise CatinatBatchBlocked("batch_event_kind_invalid")
        lot = str(lot_reference or "").strip()
        if not _LOT.fullmatch(lot):
            raise CatinatBatchBlocked("lot_reference_required")
        why = _note(note)
        event = _time(event_at, required=True)
        packaging = _time(package_expiry_at, required=False)
        if event > datetime.now(timezone.utc):
            raise CatinatBatchBlocked("event_time_cannot_be_in_future")
        try:
            digest, spreadsheet, row = _source_row(
                current_readiness(), business_date=business_date,
                sheet=sheet, cell=cell, expected_hash=expected_source_hash,
            )
        except (CatinatCountBlocked, TypeError) as exc:
            raise CatinatBatchBlocked("invalid_batch_source") from exc
        values = {
            "source_hash": digest, "spreadsheet_id": spreadsheet,
            "sheet": sheet, "cell": cell, "material": row["material"],
            "form": row["form"], "unit": row["unit"], "lot_reference": lot,
            "event_kind": event_kind, "event_at": event.isoformat(),
            "package_expiry_at": packaging.isoformat() if packaging else None, "note": why,
        }
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.sessions() as session:
            try:
                with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                            "key": f"catinat-batch:{tenant_id}:{idempotency_key}",
                        })
                    existing = session.scalar(select(InventoryCatinatBatchEvidenceModel).where(
                        InventoryCatinatBatchEvidenceModel.tenant_id == tenant_id,
                        InventoryCatinatBatchEvidenceModel.idempotency_key == idempotency_key,
                    ))
                    if existing is not None:
                        if existing.attested_by != actor_id or existing.payload_hash != fingerprint:
                            raise CatinatBatchBlocked("batch_evidence_idempotency_conflict")
                        return _public(existing, replay=True, current_source_hash=digest)
                    record = InventoryCatinatBatchEvidenceModel(
                        tenant_id=tenant_id, idempotency_key=idempotency_key,
                        business_date=business_date, spreadsheet_file_id=spreadsheet,
                        sheet_name=sheet, cell_ref=cell, source_hash=digest,
                        material_display=row["material"], form=row["form"], unit=row["unit"],
                        lot_reference=lot, event_kind=event_kind, event_at=event,
                        package_expiry_at=packaging, note=why, payload_hash=fingerprint,
                        attested_by=actor_id, attested_at=inventory_utcnow(),
                        status="staff_attested_unverified",
                    )
                    session.add(record)
                    session.flush()
                    return _public(record, current_source_hash=digest)
            except IntegrityError as exc:
                raise CatinatBatchBlocked("batch_evidence_concurrent_conflict") from exc

    def list(self, *, tenant_id: str, business_date: date,
             current_readiness: Callable[[], dict]) -> dict:
        try:
            digest, workbook = _source(current_readiness(), business_date)
        except CatinatCountBlocked as exc:
            raise CatinatBatchBlocked(exc.code) from exc
        with self.sessions() as session:
            rows = session.scalars(select(InventoryCatinatBatchEvidenceModel).where(
                InventoryCatinatBatchEvidenceModel.tenant_id == tenant_id,
                InventoryCatinatBatchEvidenceModel.business_date == business_date,
                InventoryCatinatBatchEvidenceModel.spreadsheet_file_id == workbook,
            ).order_by(InventoryCatinatBatchEvidenceModel.attested_at.desc(),
                       InventoryCatinatBatchEvidenceModel.id.desc()).limit(200)).all()
            return {
                "source_hash": digest, "business_date": business_date.isoformat(),
                "items": [_public(row, current_source_hash=digest) for row in rows],
                "mode": "batch_staff_evidence_only", "ready_to_sign": False,
                "inventory_writes": 0, "sheets_writes": 0,
            }

    def request_disposal(self, *, tenant_id: str, actor_id: str, batch_id: str,
                         business_date: date, expected_source_hash: str,
                         idempotency_key: str, quantity: str, reason: str,
                         current_readiness: Callable[[], dict]) -> dict:
        why = _note(reason)
        amount = _decimal(quantity)
        with self.sessions() as session:
            record = session.scalar(select(InventoryCatinatBatchEvidenceModel).where(
                InventoryCatinatBatchEvidenceModel.id == batch_id,
                InventoryCatinatBatchEvidenceModel.tenant_id == tenant_id,
                InventoryCatinatBatchEvidenceModel.business_date == business_date,
            ))
            if record is None:
                raise CatinatBatchBlocked("batch_evidence_not_found")
            # Preserve a copy of source-bound evidence, never infer disposed lot qty.
            values = {key: getattr(record, key) for key in (
                "source_hash", "spreadsheet_file_id", "sheet_name", "cell_ref",
                "material_display", "form", "unit", "lot_reference",
            )}
        try:
            digest, workbook, row = _source_row(
                current_readiness(), business_date=business_date,
                sheet=values["sheet_name"], cell=values["cell_ref"],
                expected_hash=expected_source_hash,
            )
        except (CatinatCountBlocked, TypeError) as exc:
            raise CatinatBatchBlocked("invalid_batch_source") from exc
        if (digest != values["source_hash"] or workbook != values["spreadsheet_file_id"]
                or row["material"] != values["material_display"]
                or row["form"] != values["form"] or row["unit"] != values["unit"]):
            raise CatinatBatchBlocked("batch_source_evidence_stale")
        proposal = {
            "batch_evidence_id": batch_id,
            "source_hash": digest, "sheet": values["sheet_name"],
            "cell": values["cell_ref"], "material": values["material_display"],
            "unit": values["unit"], "lot_reference": values["lot_reference"],
            "quantity_requested": format(amount, "f"),
            "disposal_physically_verified": False,
            "ledger_posted": False, "sheets_writes": 0,
        }
        return CatinatApprovalService(self.sessions).request(
            tenant_id=tenant_id, actor_id=actor_id, idempotency_key=idempotency_key,
            business_date=business_date, kind="waste_disposal",
            subject_ref=batch_id, proposal=proposal, reason=why,
            trusted_waste_source=True,
        )
