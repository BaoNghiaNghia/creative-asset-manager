from datetime import date
import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.auth_persistence.model import TenantModel
from app.modules.inventory.daily.catinat_policy import ClosingItem, InventoryPolicyBlocked
from app.modules.inventory.daily.catinat_signed_shift import CatinatSignedShiftService
from app.modules.inventory.persistence_model import InventoryCatinatSignedShiftModel

DAY = date(2026, 10, 20)
SOURCE_HASH = hashlib.sha256(b"source-evidence").hexdigest()


@pytest.fixture
def service():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[TenantModel.__table__, InventoryCatinatSignedShiftModel.__table__],
    )
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(TenantModel(id="tenant-1", name="Catinat QA", slug="catinat-qa"))
    yield CatinatSignedShiftService(sessions), sessions
    engine.dispose()


def _item(raw="/", confirmed_by="staff"):
    return ClosingItem(
        material_id="milk", location_id="bar", unit="lon",
        physical_raw=raw, confirmed_by=confirmed_by,
    )


def _sign(service, **overrides):
    kwargs = dict(
        tenant_id="tenant-1", business_date=DAY, shift="evening",
        actor_id="staff", spreadsheet_file_id="sheet-1",
        expected_source_hash=SOURCE_HASH,
        server_source_hash_reader=lambda: SOURCE_HASH,
        items=[_item()],
    )
    kwargs.update(overrides)
    return service.sign(**kwargs)


def test_signed_snapshot_is_unique_and_idempotent(service):
    signer, sessions = service
    first = _sign(signer)
    assert not first.idempotent_replay
    replay = _sign(signer)
    assert replay.id == first.id
    assert replay.idempotent_replay
    with sessions() as session:
        rows = list(session.scalars(select(InventoryCatinatSignedShiftModel)))
        assert len(rows) == 1
        assert rows[0].counts_json[0]["physical_quantity"] == "0"
        assert rows[0].confirmed_by == "staff"
        assert rows[0].source_hash == SOURCE_HASH


def test_morning_and_evening_have_separate_snapshot_keys(service):
    signer, sessions = service
    morning = _sign(signer, shift="morning")
    evening = _sign(signer, shift="evening")
    assert morning.id != evening.id
    with sessions() as session:
        assert len(list(session.scalars(select(InventoryCatinatSignedShiftModel)))) == 2


def test_second_close_cannot_modify_existing_snapshot(service):
    signer, sessions = service
    before = _sign(signer)
    with pytest.raises(InventoryPolicyBlocked, match="immutable_shift_snapshot_conflict"):
        _sign(signer, items=[_item(2)])
    with sessions() as session:
        row = session.get(InventoryCatinatSignedShiftModel, before.id)
        assert row.snapshot_hash == before.snapshot_hash
        assert row.counts_json[0]["physical_quantity"] == "0"


def test_sheets_changed_since_staff_read_blocks_closure(service):
    signer, sessions = service
    with pytest.raises(InventoryPolicyBlocked, match="stale_google_sheet_evidence"):
        _sign(signer, server_source_hash_reader=lambda: hashlib.sha256(b"new").hexdigest())
    with sessions() as session:
        assert not list(session.scalars(select(InventoryCatinatSignedShiftModel)))


@pytest.mark.parametrize("kwargs,reason", [
    ({"actor_id": ""}, "signed_shift_identity_required"),
    ({"expected_source_hash": "unverified"}, "invalid_expected_sheet_fingerprint"),
    ({"items": [_item(confirmed_by="other")]}, "staff_count_actor_mismatch"),
    ({"items": [_item(raw=None)]}, "invalid_or_missing_physical_count"),
    ({"items": [_item(raw="<200ml")]}, "invalid_or_missing_physical_count"),
    ({"pending_transfer_ids": ("transfer-1",)}, "pending_transfer_requires_manager_exception"),
    ({"items": [_item()] * 2}, "missing_or_duplicate_stock_identity"),
    ({"items": [ClosingItem(material_id="milk", location_id="bar", unit="lon",
                            physical_raw=1, confirmed_by="staff",
                            discrepancy_unresolved=True,
                            discrepancy_proposed_by="staff",
                            discrepancy_approval_by="boss")]}, "manager_discrepancy_approval_required"),
])
def test_untrusted_or_incomplete_confirmations_are_rejected(service, kwargs, reason):
    signer, _sessions = service
    with pytest.raises(InventoryPolicyBlocked, match=reason):
        _sign(signer, **kwargs)
