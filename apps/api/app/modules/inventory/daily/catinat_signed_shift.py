"""Persist unique immutable staff-signed Catinat shift counts (Q16/Q22/Q28/Q39).

No Google Sheets edits, no auto-close. The caller must authenticate the actor
and provide a server-side fresh source hash reader bound to the tenant/workbook.
Manager exception approval must be implemented in a trusted DB workflow before
signing a shift that has exceptions; this module deliberately fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from typing import Callable, Sequence

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_policy import (
    ClosingItem, InventoryPolicyBlocked, amount, preflight_shift_close,
)
from app.modules.inventory.persistence_model import (
    InventoryCatinatSignedShiftModel, inventory_utcnow,
)

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class SignedShiftRecord:
    id: str
    business_date: date
    shift: str
    snapshot_hash: str
    confirmed_by: str
    idempotent_replay: bool


def _signable(
    *, tenant_id: str, business_date: date, shift: str,
    spreadsheet_file_id: str, source_hash: str, actor_id: str,
    items: Sequence[ClosingItem],
) -> tuple[str, list[dict]]:
    rows = sorted(
        ({
            "material_id": i.material_id,
            "location_id": i.location_id,
            "unit": i.unit,
            "physical_quantity": format(amount(i.physical_raw, zero_slash=True), "f"),
            "confirmed_by": i.confirmed_by,
        } for i in items),
        key=lambda x: (x["material_id"], x["location_id"], x["unit"]),
    )
    encoded = json.dumps({
        "tenant_id": tenant_id, "business_date": business_date.isoformat(),
        "shift": shift, "spreadsheet_file_id": spreadsheet_file_id,
        "source_hash": source_hash, "confirmed_by": actor_id, "counts": rows,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), rows


class CatinatSignedShiftService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def sign(
        self, *, tenant_id: str, business_date: date, shift: str,
        actor_id: str, spreadsheet_file_id: str, expected_source_hash: str,
        server_source_hash_reader: Callable[[], str],
        items: Sequence[ClosingItem],
        pending_transfer_ids: tuple[str, ...] = (),
    ) -> SignedShiftRecord:
        if not tenant_id.strip() or not actor_id.strip() or not spreadsheet_file_id.strip():
            raise InventoryPolicyBlocked("signed_shift_identity_required")
        if not _SHA256.fullmatch(expected_source_hash):
            raise InventoryPolicyBlocked("invalid_expected_sheet_fingerprint")
        # The external source verifier MUST read the live, bound workbook after
        # authenticating the tenant. Never trust a source_fresh flag from users.
        latest_hash = server_source_hash_reader()
        if not _SHA256.fullmatch(latest_hash):
            raise InventoryPolicyBlocked("invalid_server_sheet_fingerprint")
        fresh = latest_hash == expected_source_hash
        if any(item.confirmed_by != actor_id for item in items):
            raise InventoryPolicyBlocked("staff_count_actor_mismatch")
        # Trusted manager approvals and durable transfer reconciliation are not
        # wired yet: no exceptions may be silently approved via request fields.
        if pending_transfer_ids:
            raise InventoryPolicyBlocked("pending_transfer_requires_manager_exception")
        if any(item.discrepancy_unresolved for item in items):
            raise InventoryPolicyBlocked("manager_discrepancy_approval_required")
        gate = preflight_shift_close(
            business_date=business_date, shift=shift, closing_actor=actor_id,
            items=list(items), source_fresh=fresh,
        )
        if not gate.ready:
            raise InventoryPolicyBlocked(",".join(gate.blockers))
        digest, normalized = _signable(
            tenant_id=tenant_id, business_date=business_date, shift=shift,
            spreadsheet_file_id=spreadsheet_file_id,
            source_hash=expected_source_hash, actor_id=actor_id, items=items,
        )
        with self.sessions() as session:
            try:
                with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                            {"key": f"catinat-sign:{tenant_id}:{business_date}:{shift}"},
                        )
                    existing = session.scalar(select(InventoryCatinatSignedShiftModel).where(
                        InventoryCatinatSignedShiftModel.tenant_id == tenant_id,
                        InventoryCatinatSignedShiftModel.business_date == business_date,
                        InventoryCatinatSignedShiftModel.shift == shift,
                    ))
                    if existing is not None:
                        return self._replay(existing, digest, actor_id)
                    entry = InventoryCatinatSignedShiftModel(
                        tenant_id=tenant_id, business_date=business_date, shift=shift,
                        source_spreadsheet_file_id=spreadsheet_file_id,
                        source_hash=expected_source_hash, snapshot_hash=digest,
                        counts_json=normalized, confirmed_by=actor_id,
                        confirmed_at=inventory_utcnow(), created_at=inventory_utcnow(),
                    )
                    session.add(entry)
                    session.flush()
                    result = SignedShiftRecord(
                        entry.id, business_date, shift, digest, actor_id, False,
                    )
                return result
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(InventoryCatinatSignedShiftModel).where(
                    InventoryCatinatSignedShiftModel.tenant_id == tenant_id,
                    InventoryCatinatSignedShiftModel.business_date == business_date,
                    InventoryCatinatSignedShiftModel.shift == shift,
                ))
                if existing is None:
                    raise
                return self._replay(existing, digest, actor_id)

    @staticmethod
    def _replay(
        existing: InventoryCatinatSignedShiftModel, digest: str, actor_id: str,
    ) -> SignedShiftRecord:
        if existing.snapshot_hash != digest or existing.confirmed_by != actor_id:
            raise InventoryPolicyBlocked("immutable_shift_snapshot_conflict")
        return SignedShiftRecord(
            existing.id, existing.business_date, existing.shift,
            existing.snapshot_hash, existing.confirmed_by, True,
        )
