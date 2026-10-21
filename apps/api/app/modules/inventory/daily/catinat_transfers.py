"""Q32 auditable transfer in-transit tracker; NEVER credits destination stock.

This is an evidence tracker, not the authoritative ledger committer. Only
transfer-out and receiver-confirmed receipt facts are persisted; no
InventoryTransactionModel or Google Sheets write occurs here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import re

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_policy import amount, InventoryPolicyBlocked
from app.modules.inventory.persistence_model import (
    InventoryCatinatTransferModel, InventoryCatinatTransferEventModel,
    InventoryItemModel, InventoryLocationModel, inventory_utcnow,
)

KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class CatinatTransferBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: str, kind: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 255:
        raise CatinatTransferBlocked(kind)
    return result


def _key(value: str) -> str:
    if not KEY.fullmatch(str(value or "")):
        raise CatinatTransferBlocked("invalid_transfer_event_key")
    return str(value)


def _quantity(value) -> Decimal:
    try:
        parsed = amount(value, zero_slash=False)
    except InventoryPolicyBlocked as exc:
        raise CatinatTransferBlocked("invalid_transfer_quantity") from exc
    if parsed <= 0 or parsed.as_tuple().exponent < -8 or parsed >= Decimal("1e16"):
        raise CatinatTransferBlocked("invalid_transfer_quantity")
    return parsed


def _view(row: InventoryCatinatTransferModel, *, replay: bool = False) -> dict:
    remaining = row.dispatched_quantity - row.received_quantity
    return {
        "id": row.id,
        "business_date": row.business_date.isoformat(),
        "item_id": row.item_id,
        "source_location_id": row.source_location_id,
        "destination_location_id": row.destination_location_id,
        "unit": row.unit,
        "dispatched_quantity": format(row.dispatched_quantity, "f"),
        "received_quantity": format(row.received_quantity, "f"),
        "in_transit_quantity": format(remaining, "f"),
        "status": row.status,
        "dispatched_by": row.dispatched_by,
        "dispatched_at": row.dispatched_at.isoformat() if row.dispatched_at else None,
        "received_last_at": row.received_last_at.isoformat() if row.received_last_at else None,
        "idempotent_replay": replay,
        "mode": "handoff_tracking_only",
        "ledger_posted": bool(row.ledger_posted),
        "inventory_write_performed": False,
        "sheets_writes": 0,
    }


class CatinatTransferService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def dispatch(
        self, *, tenant_id: str, actor_id: str, reference_key: str,
        business_date: date, item_id: str, source_location_id: str,
        destination_location_id: str, unit: str, quantity: str,
    ) -> dict:
        tenant = _text(tenant_id, "tenant_required")
        actor = _text(actor_id, "actor_required")
        ref = _key(reference_key)
        item = _text(item_id, "material_id_required")
        source = _text(source_location_id, "source_location_required")
        destination = _text(destination_location_id, "destination_location_required")
        checked_unit = _text(unit, "unit_required")
        if source == destination:
            raise CatinatTransferBlocked("transfer_same_location")
        qty = _quantity(quantity)
        with self.sessions() as session:
            try:
                with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                            {"key": f"catinat-dispatch:{tenant}:{ref}"},
                        )
                    old = session.scalar(select(InventoryCatinatTransferModel).where(
                        InventoryCatinatTransferModel.tenant_id == tenant,
                        InventoryCatinatTransferModel.reference_key == ref,
                    ))
                    if old is not None:
                        if (old.business_date != business_date
                            or old.item_id != item or old.source_location_id != source
                            or old.destination_location_id != destination
                            or old.unit != checked_unit or old.dispatched_quantity != qty
                            or old.dispatched_by != actor):
                            raise CatinatTransferBlocked("transfer_idempotency_conflict")
                        return _view(old, replay=True)
                    material = session.scalar(select(InventoryItemModel).where(
                        InventoryItemModel.tenant_id == tenant,
                        InventoryItemModel.id == item,
                        InventoryItemModel.active.is_(True),
                    ))
                    if material is None:
                        raise CatinatTransferBlocked("approved_active_material_required")
                    if checked_unit not in {
                        material.base_unit, material.whole_unit, material.fraction_unit
                    }:
                        raise CatinatTransferBlocked("material_unit_mismatch")
                    locations = list(session.scalars(select(InventoryLocationModel).where(
                        InventoryLocationModel.tenant_id == tenant,
                        InventoryLocationModel.id.in_([source, destination]),
                        InventoryLocationModel.active.is_(True),
                    )))
                    if len(locations) != 2:
                        raise CatinatTransferBlocked("approved_active_locations_required")
                    now = inventory_utcnow()
                    row = InventoryCatinatTransferModel(
                        tenant_id=tenant, reference_key=ref, business_date=business_date,
                        item_id=item, source_location_id=source,
                        destination_location_id=destination, unit=checked_unit,
                        dispatched_quantity=qty, received_quantity=Decimal(0),
                        status="pending_receipt", dispatched_by=actor,
                        dispatched_at=now, ledger_posted=False,
                    )
                    session.add(row)
                    session.flush()
                    session.add(InventoryCatinatTransferEventModel(
                        tenant_id=tenant, transfer_id=row.id, event_key=ref,
                        action="dispatched", quantity=qty, actor_id=actor,
                        created_at=now,
                    ))
                    session.flush()
                    return _view(row)
            except IntegrityError as exc:
                raise CatinatTransferBlocked("concurrent_dispatch_conflict") from exc

    def receive(
        self, *, tenant_id: str, actor_id: str,
        transfer_id: str, event_key: str, quantity: str,
    ) -> dict:
        tenant = _text(tenant_id, "tenant_required")
        actor = _text(actor_id, "receiver_confirmation_required")
        ref = _text(transfer_id, "invalid_transfer_id")
        event = _key(event_key)
        received = _quantity(quantity)
        with self.sessions() as session:
            try:
                with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                            {"key": f"catinat-receive:{tenant}:{ref}"},
                        )
                    query = select(InventoryCatinatTransferModel).where(
                        InventoryCatinatTransferModel.tenant_id == tenant,
                        InventoryCatinatTransferModel.id == ref,
                    )
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        query = query.with_for_update()
                    row = session.scalar(query)
                    if row is None:
                        raise CatinatTransferBlocked("transfer_not_found")
                    old = session.scalar(select(InventoryCatinatTransferEventModel).where(
                        InventoryCatinatTransferEventModel.tenant_id == tenant,
                        InventoryCatinatTransferEventModel.transfer_id == ref,
                        InventoryCatinatTransferEventModel.event_key == event,
                    ))
                    if old is not None:
                        if (old.action != "received" or old.quantity != received
                                or old.actor_id != actor):
                            raise CatinatTransferBlocked("transfer_event_idempotency_conflict")
                        return _view(row, replay=True)
                    remaining = row.dispatched_quantity - row.received_quantity
                    if received > remaining:
                        raise CatinatTransferBlocked("receipt_exceeds_in_transit")
                    row.received_quantity += received
                    row.status = ("received" if row.received_quantity == row.dispatched_quantity
                                  else "partial_receipt")
                    row.received_last_at = inventory_utcnow()
                    session.add(InventoryCatinatTransferEventModel(
                        tenant_id=tenant, transfer_id=ref, event_key=event,
                        action="received", quantity=received, actor_id=actor,
                        created_at=row.received_last_at,
                    ))
                    session.flush()
                    return _view(row)
            except IntegrityError as exc:
                raise CatinatTransferBlocked("concurrent_receipt_conflict") from exc

    def list(self, *, tenant_id: str, business_date: date | None = None,
             pending_only: bool = False, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise CatinatTransferBlocked("invalid_transfer_list_limit")
        with self.sessions() as session:
            query = select(InventoryCatinatTransferModel).where(
                InventoryCatinatTransferModel.tenant_id == tenant_id
            )
            if business_date:
                query = query.where(
                    InventoryCatinatTransferModel.business_date == business_date)
            if pending_only:
                query = query.where(InventoryCatinatTransferModel.status != "received")
            rows = session.scalars(query.order_by(
                InventoryCatinatTransferModel.dispatched_at.desc(),
                InventoryCatinatTransferModel.id.desc(),
            ).limit(limit)).all()
            return [_view(row) for row in rows]

    def get(self, *, tenant_id: str, transfer_id: str) -> dict:
        with self.sessions() as session:
            row = session.scalar(select(InventoryCatinatTransferModel).where(
                InventoryCatinatTransferModel.tenant_id == tenant_id,
                InventoryCatinatTransferModel.id == transfer_id,
            ))
            if row is None:
                raise CatinatTransferBlocked("transfer_not_found")
            return _view(row)
