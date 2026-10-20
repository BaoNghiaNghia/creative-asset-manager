from datetime import date
from decimal import Decimal

import pytest

from app.modules.inventory.daily.catinat_policy import (
    ClosingItem, InventoryPolicyBlocked, amount,
    evaluate_transfer, open_approved_package, preflight_shift_close,
    transfer_within_batch,
)

DAY = date(2026, 10, 20)


def test_slash_is_zero_but_blank_is_not_zero():
    assert amount("/", zero_slash=True) == Decimal(0)
    with pytest.raises(InventoryPolicyBlocked):
        amount("")
    with pytest.raises(InventoryPolicyBlocked):
        amount("/", zero_slash=False)


@pytest.mark.parametrize("invalid", ["1,234", "<200ml", "22+39", "14/10", "NaN", True, "-1"])
def test_ambiguous_stock_never_silently_parsed(invalid):
    with pytest.raises(InventoryPolicyBlocked):
        amount(invalid, zero_slash=True)


def test_package_opening_conserves_whole_and_loose_before_consumption():
    result = open_approved_package(
        whole_before=6, loose_before=0, count_opened=1,
        approved_loose_per_whole=380, actual_consumed=100,
        actor="staff", conversion_approval_id="approved-catalog-v1",
    )
    assert (result.whole_after, result.loose_after, result.consumed) == (
        Decimal(5), Decimal(280), Decimal(100))
    assert 6 * 380 == result.whole_after * 380 + result.loose_after + result.consumed


@pytest.mark.parametrize("kwargs,code", [
    ({"count_opened": 7}, "invalid_package_opening"),
    ({"count_opened": "0.5"}, "invalid_package_opening"),
    ({"approved_loose_per_whole": 0}, "invalid_package_opening"),
    ({"actual_consumed": 500}, "consumption_exceeds_loose_stock"),
    ({"conversion_approval_id": ""}, "opening_or_conversion_unconfirmed"),
])
def test_package_opening_rejects_unapproved_or_invalid(kwargs, code):
    values = dict(whole_before=6, loose_before=0, count_opened=1,
                  approved_loose_per_whole=380, actual_consumed=100,
                  actor="staff", conversion_approval_id="approved")
    values.update(kwargs)
    with pytest.raises(InventoryPolicyBlocked, match=code):
        open_approved_package(**values)


def test_transfer_pending_is_not_received_stock():
    result = evaluate_transfer(source_before=10, destination_before=2, dispatched=6)
    assert (result.source_available, result.destination_available, result.in_transit) == (
        Decimal(4), Decimal(2), Decimal(6))
    assert result.status == "pending_receipt"
    assert sum((result.source_available, result.destination_available, result.in_transit)) == 12


def test_confirmed_transfer_is_reconciled():
    result = evaluate_transfer(
        source_before=10, destination_before=2, dispatched=6,
        received=6, receiving_actor="receiver",
    )
    assert (result.source_available, result.destination_available, result.in_transit) == (
        Decimal(4), Decimal(8), Decimal(0))
    assert result.status == "received"


def test_partial_transfer_is_not_silent_loss():
    result = evaluate_transfer(
        source_before=10, destination_before=2, dispatched=6,
        received=5, receiving_actor="receiver",
    )
    assert result.status == "mismatch"
    assert result.shortage == result.in_transit == Decimal(1)
    assert sum((result.source_available, result.destination_available, result.in_transit)) == 12


def test_receipt_requires_actor_and_sane_quantity():
    with pytest.raises(InventoryPolicyBlocked, match="receipt_confirmation_required"):
        evaluate_transfer(source_before=6, destination_before=0, dispatched=2, received=2)
    with pytest.raises(InventoryPolicyBlocked, match="receipt_exceeds_dispatch"):
        evaluate_transfer(source_before=6, destination_before=0, dispatched=2, received=3, receiving_actor="receiver")


def test_transfer_same_batch_preserves_quantity():
    result = transfer_within_batch(
        source_quantity="657,4", destination_quantity="730,2", moved="100",
        source_batch_id="batch-14-10", destination_batch_id="batch-14-10", actor="staff",
    )
    assert result.source_after == Decimal("557.4")
    assert result.destination_after == Decimal("830.2")
    assert result.source_after + result.destination_after == Decimal("1387.6")


def test_different_batches_cannot_be_mixed_even_on_same_date():
    with pytest.raises(InventoryPolicyBlocked, match="cross_batch_mixing_forbidden"):
        transfer_within_batch(
            source_quantity=100, destination_quantity=200, moved=20,
            source_batch_id="batch-A", destination_batch_id="batch-B", actor="staff",
        )


def _item(**overrides):
    kwargs = dict(material_id="milk", location_id="bar", unit="lon",
                  physical_raw="/", confirmed_by="staff")
    kwargs.update(overrides)
    return ClosingItem(**kwargs)


def _gate(items=None, **extra):
    args = dict(business_date=DAY, shift="evening", closing_actor="staff",
                items=[_item()] if items is None else items, source_fresh=True)
    args.update(extra)
    return preflight_shift_close(**args)


def test_verified_zero_may_close():
    assert _gate().ready


@pytest.mark.parametrize("overrides,reason", [
    ({"physical_raw": None}, "invalid_or_missing_physical_count"),
    ({"physical_raw": "<200ml"}, "invalid_or_missing_physical_count"),
    ({"confirmed_by": None}, "unconfirmed_physical_count"),
    ({"expired_unresolved": True}, "expired_batch_requires_verified_disposal"),
    ({"discrepancy_unresolved": True}, "manager_discrepancy_approval_required"),
    ({"discrepancy_unresolved": True, "discrepancy_approval_by": "boss"},
     "manager_discrepancy_approval_required"),
    ({"discrepancy_unresolved": True, "discrepancy_proposed_by": "boss",
      "discrepancy_approval_by": "boss"}, "manager_discrepancy_approval_required"),
])
def test_closing_blocks_unverified_or_unapproved(overrides, reason):
    result = _gate([_item(**overrides)])
    assert not result.ready
    assert reason in result.blockers


def test_valid_independent_manager_can_approve_discrepancy():
    result = _gate([_item(discrepancy_unresolved=True, discrepancy_proposed_by="staff",
                          discrepancy_approval_by="manager")])
    assert result.ready


def test_pending_transfer_requires_separate_manager_exception():
    assert "pending_transfer_requires_manager_exception" in _gate(
        pending_transfer_ids=("tx-1",)).blockers
    assert "pending_transfer_requires_manager_exception" in _gate(
        pending_transfer_ids=("tx-1",), transfer_exception_manager="staff",
        transfer_exception_requestor="staff").blockers
    assert "pending_transfer_requires_manager_exception" in _gate(
        pending_transfer_ids=("tx-1",), transfer_exception_manager="boss").blockers
    assert _gate(
        pending_transfer_ids=("tx-1",), transfer_exception_manager="boss",
        transfer_exception_requestor="staff").ready


def test_shift_close_requires_fresh_sheet_and_explicit_staff_action():
    result = _gate(source_fresh=False, closing_actor="")
    assert {"stale_google_sheet_evidence", "staff_confirmation_required"} <= set(result.blockers)


def test_duplicate_ids_and_empty_counts_blocked():
    result = _gate([_item(), _item()])
    assert "missing_or_duplicate_stock_identity" in result.blockers
    assert "physical_counts_required" in _gate([]).blockers
