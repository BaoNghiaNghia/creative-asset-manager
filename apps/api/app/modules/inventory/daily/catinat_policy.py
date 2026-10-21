"""Deterministic, side-effect-free Catinat accounting gates (Q01-Q47).

These helpers are not a Sheets writer or a physical-count certificate.
Persist confirmations/events in the approved ledger before a real close.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class InventoryPolicyBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def amount(raw: Any, *, zero_slash: bool = False) -> Decimal:
    if isinstance(raw, bool) or raw is None or raw == "":
        raise InventoryPolicyBlocked("quantity_missing_or_invalid")
    if isinstance(raw, str):
        text = raw.strip()
        if zero_slash and text == "/":
            return Decimal(0)
        # 1,234 may mean 1234 or 1.234: never guess.
        if not text or any(ch not in "0123456789.," for ch in text):
            raise InventoryPolicyBlocked("quantity_missing_or_invalid")
        if text.count(".") + text.count(",") > 1:
            raise InventoryPolicyBlocked("quantity_ambiguous")
        if "," in text and len(text.rsplit(",", 1)[-1]) == 3:
            raise InventoryPolicyBlocked("quantity_ambiguous")
        text = text.replace(",", ".")
    else:
        text = str(raw)
    try:
        value = Decimal(text)
    except (ValueError, InvalidOperation) as exc:
        raise InventoryPolicyBlocked("quantity_missing_or_invalid") from exc
    if not value.is_finite() or value < 0:
        raise InventoryPolicyBlocked("quantity_missing_or_invalid")
    return value


@dataclass(frozen=True)
class PackageOpened:
    whole_after: Decimal
    loose_after: Decimal
    consumed: Decimal
    actor: str
    conversion_approval_id: str


def open_approved_package(
    *, whole_before: Any, loose_before: Any, count_opened: Any,
    approved_loose_per_whole: Any, actual_consumed: Any,
    actor: str, conversion_approval_id: str,
) -> PackageOpened:
    """Q31: opening packages conserves stock; consumption is separate."""
    if not actor.strip() or not conversion_approval_id.strip():
        raise InventoryPolicyBlocked("opening_or_conversion_unconfirmed")
    whole = amount(whole_before, zero_slash=True)
    loose = amount(loose_before, zero_slash=True)
    opened = amount(count_opened)
    factor = amount(approved_loose_per_whole)
    consumed = amount(actual_consumed, zero_slash=True)
    if opened != opened.to_integral_value() or opened <= 0 or factor <= 0 or opened > whole:
        raise InventoryPolicyBlocked("invalid_package_opening")
    available_loose = loose + opened * factor
    if consumed > available_loose:
        raise InventoryPolicyBlocked("consumption_exceeds_loose_stock")
    return PackageOpened(whole - opened, available_loose - consumed, consumed, actor, conversion_approval_id)


@dataclass(frozen=True)
class TransferBalances:
    source_available: Decimal
    destination_available: Decimal
    in_transit: Decimal
    status: str
    shortage: Decimal


def evaluate_transfer(
    *, source_before: Any, destination_before: Any, dispatched: Any,
    received: Any | None = None, receiving_actor: str | None = None,
) -> TransferBalances:
    """Q32: never credit destination until confirmed receipt.

    For a partial receipt, remaining items stay in transit and mismatch is visible.
    Caller must persist a unique transfer ID and the separate send/receive events.
    """
    source, dest, sent = (amount(x, zero_slash=True) for x in (source_before, destination_before, dispatched))
    if sent <= 0 or sent > source:
        raise InventoryPolicyBlocked("invalid_transfer_dispatch")
    if received is None:
        if receiving_actor:
            raise InventoryPolicyBlocked("receipt_quantity_required")
        return TransferBalances(source - sent, dest, sent, "pending_receipt", Decimal(0))
    if not receiving_actor or not receiving_actor.strip():
        raise InventoryPolicyBlocked("receipt_confirmation_required")
    got = amount(received, zero_slash=True)
    if got > sent:
        raise InventoryPolicyBlocked("receipt_exceeds_dispatch")
    return TransferBalances(source - sent, dest + got, sent - got, "received" if got == sent else "mismatch", sent - got)


@dataclass(frozen=True)
class ContainerBalances:
    source_after: Decimal
    destination_after: Decimal


def transfer_within_batch(
    *, source_quantity: Any, destination_quantity: Any, moved: Any,
    source_batch_id: str, destination_batch_id: str, actor: str,
) -> ContainerBalances:
    """Q36: do not let a container move relabel or renew a batch."""
    if not source_batch_id or not destination_batch_id or source_batch_id != destination_batch_id:
        raise InventoryPolicyBlocked("cross_batch_mixing_forbidden")
    if not actor.strip():
        raise InventoryPolicyBlocked("container_transfer_unconfirmed")
    source = amount(source_quantity, zero_slash=True)
    destination = amount(destination_quantity, zero_slash=True)
    quantity = amount(moved)
    if quantity <= 0 or quantity > source:
        raise InventoryPolicyBlocked("invalid_container_transfer")
    return ContainerBalances(source - quantity, destination + quantity)


@dataclass(frozen=True)
class ClosingItem:
    material_id: str
    location_id: str
    unit: str
    physical_raw: Any
    confirmed_by: str | None
    expired_unresolved: bool = False
    discrepancy_unresolved: bool = False
    discrepancy_approval_by: str | None = None
    discrepancy_proposed_by: str | None = None


@dataclass(frozen=True)
class ClosingGate:
    ready: bool
    blockers: tuple[str, ...]
    business_date: date
    shift: str


def preflight_shift_close(
    *, business_date: date, shift: str, closing_actor: str | None,
    items: list[ClosingItem], pending_transfer_ids: tuple[str, ...] = (),
    transfer_exception_manager: str | None = None,
    transfer_exception_requestor: str | None = None,
    source_fresh: bool,
) -> ClosingGate:
    """Q02/Q16/Q19/Q27/Q28/Q32/Q38/Q39/Q44: strict validation only.

    No write to Sheets; caller must preserve immutable unique snapshot and
    coordinate a readback-protected carry-forward after explicit signed close.
    """
    blockers: set[str] = set()
    if shift not in {"morning", "evening"}:
        blockers.add("invalid_shift")
    if not closing_actor or not closing_actor.strip():
        blockers.add("staff_confirmation_required")
    if not source_fresh:
        blockers.add("stale_google_sheet_evidence")
    if not items:
        blockers.add("physical_counts_required")
    identity: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item.material_id, item.location_id, item.unit)
        if not all(key) or key in identity:
            blockers.add("missing_or_duplicate_stock_identity")
        identity.add(key)
        try:
            amount(item.physical_raw, zero_slash=True)
        except InventoryPolicyBlocked:
            blockers.add("invalid_or_missing_physical_count")
        if not item.confirmed_by or not item.confirmed_by.strip():
            blockers.add("unconfirmed_physical_count")
        if item.expired_unresolved:
            blockers.add("expired_batch_requires_verified_disposal")
        if item.discrepancy_unresolved:
            if not item.discrepancy_approval_by or not item.discrepancy_proposed_by or (
                item.discrepancy_approval_by == item.discrepancy_proposed_by
            ):
                blockers.add("manager_discrepancy_approval_required")
    if pending_transfer_ids and (
        not transfer_exception_manager or
        not transfer_exception_requestor or
        transfer_exception_manager == transfer_exception_requestor
    ):
        blockers.add("pending_transfer_requires_manager_exception")
    return ClosingGate(not blockers, tuple(sorted(blockers)), business_date, shift)
