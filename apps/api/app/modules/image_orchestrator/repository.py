"""Tenant-scoped durable aggregate transitions, P1 candidate.

Call from an explicit short transaction; no renderer/Drive network calls
inside a lock. PostgreSQL row locks are mandatory outside isolated tests.
Lock order: resource group (if required), flow, batch, pack IDs, role IDs.
No worker-controlled fields are accepted as authoritative proof here.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable, TypeVar
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import policy as p
from .model import ImageBatch, ImageFlowControl, ImagePack, ImageRole

T = TypeVar("T")


def _transaction(db: Session, *, test_sqlite: bool = False):
    p.require(db.in_transaction(), "transaction_required")
    dialect = db.get_bind().dialect.name
    p.require(dialect == "postgresql" or (test_sqlite and dialect == "sqlite"),
              "postgresql_locking_required")


def lock_flow(db: Session, tenant: str, *, test_sqlite: bool = False) -> ImageFlowControl:
    _transaction(db, test_sqlite=test_sqlite)
    row = db.scalar(select(ImageFlowControl).where(
        ImageFlowControl.tenant_id == tenant).with_for_update().execution_options(populate_existing=True))
    p.require(row is not None, "image_flow_not_registered")
    return row


def create_batch(db: Session, batch: p.Batch, entries: list[tuple[p.PackIdentity, str]],
                 *, test_sqlite: bool = False):
    """Internal admission after server verifies Picked, artwork, plan, legacy drain.

    The plan SHA binds the immutable role/color/placement manifest. This
    repository does not yet own the full manifest, so do not enable live claims.
    """
    _transaction(db, test_sqlite=test_sqlite)
    p.require(len(entries) == len(batch.pack_ids)
              and tuple(x.pack_id for x, _ in entries) == batch.pack_ids
              and all(x.tenant == batch.tenant and p.valid_sha(h) for x, h in entries),
              "batch_scope_mismatch")
    db.add(ImageBatch(tenant_id=batch.tenant, batch_id=batch.batch_id,
                      created_at=batch.created_at, pack_ids=list(batch.pack_ids),
                      release_decision=None))
    db.flush()
    for i, plan_sha in entries:
        db.add(ImagePack(tenant_id=i.tenant, pack_id=i.pack_id, batch_id=batch.batch_id,
                         listing_key=i.listing, artwork_sha256=i.artwork_sha,
                         source_sha256=i.source_sha, garment_sku=i.garment,
                         pack_revision=i.revision, placement_sha256=i.placement_sha,
                         skill_sha256=i.skill_sha, plan_sha256=plan_sha,
                         owner=None, epoch=0, lease_state=None,
                         wave_claims={"1": 0, "2": 0}, ack_state={}, used_attempt_ids=[]))
    db.flush()
    for i, _ in entries:
        db.add_all([ImageRole(tenant_id=i.tenant, pack_id=i.pack_id, role=n,
                             status="pending", attempts=0) for n in range(1, 14)])
    db.flush()


def _lock_batch(db: Session, tenant: str, batch_id: str) -> ImageBatch:
    row = db.scalar(select(ImageBatch).where(
        ImageBatch.tenant_id == tenant, ImageBatch.batch_id == batch_id).with_for_update().execution_options(populate_existing=True))
    p.require(row is not None, "image_batch_not_found")
    return row


def _lock_pack(db: Session, tenant: str, batch_id: str, pack_id: str) -> ImagePack:
    row = db.scalar(select(ImagePack).where(
        ImagePack.tenant_id == tenant, ImagePack.batch_id == batch_id,
        ImagePack.pack_id == pack_id).with_for_update().execution_options(populate_existing=True))
    p.require(row is not None, "image_pack_not_found")
    return row


def _load(db: Session, row: ImagePack) -> tuple[p.Pack, list[ImageRole]]:
    roles = list(db.scalars(select(ImageRole).where(
        ImageRole.tenant_id == row.tenant_id, ImageRole.pack_id == row.pack_id
    ).order_by(ImageRole.role).with_for_update().execution_options(populate_existing=True)))
    p.require([r.role for r in roles] == list(range(1, 14)), "role_state_corrupt")
    identity = p.PackIdentity(row.tenant_id, row.listing_key, row.artwork_sha256,
                              row.garment_sku, row.pack_revision, row.placement_sha256,
                              row.source_sha256, row.skill_sha256)
    p.require(identity.pack_id == row.pack_id, "pack_identity_corrupt")
    pack = p.Pack(identity=identity, owner=row.owner, epoch=row.epoch,
                  lease=p.Lease(**row.lease_state) if row.lease_state else None,
                  roles={r.role: p.Role(status=r.status, attempts=r.attempts,
                                      attempt_id=r.attempt_id,
                                      receipt=p.StoredReceipt(**r.receipt_state) if r.receipt_state else None,
                                      failure_code=r.failure_code) for r in roles},
                  wave_claims={int(k): v for k, v in row.wave_claims.items()},
                  acks=dict(row.ack_state), used_attempt_ids=set(row.used_attempt_ids))
    return pack, roles


def _store(row: ImagePack, pack: p.Pack, roles: list[ImageRole]):
    p.require(row.pack_id == pack.identity.pack_id
              and row.skill_sha256 == pack.identity.skill_sha
              and row.placement_sha256 == pack.identity.placement_sha
              and row.source_sha256 == pack.identity.source_sha, "immutable_pack_identity")
    row.owner = pack.owner
    row.epoch = pack.epoch
    # Explicit schema: only hashed lease secrets persist.
    row.lease_state = asdict(pack.lease) if pack.lease else None
    if row.lease_state:
        p.require(p.valid_sha(row.lease_state["command_hash"])
                  and p.valid_sha(row.lease_state["image_hash"]), "invalid_lease_hash")
    row.wave_claims = {str(k): v for k, v in pack.wave_claims.items()}
    row.ack_state = dict(pack.acks)
    row.used_attempt_ids = sorted(pack.used_attempt_ids)
    for r in roles:
        state = pack.roles[r.role]
        # A repository transition cannot silently replace an existing receipt.
        if r.status == "verified":
            p.require(state.status == "verified" and state.receipt is not None
                      and p.StoredReceipt(**r.receipt_state) == state.receipt,
                      "immutable_role_receipt")
        r.status, r.attempts, r.attempt_id = state.status, state.attempts, state.attempt_id
        r.failure_code = state.failure_code
        r.receipt_state = asdict(state.receipt) if state.receipt else None


def transition(db: Session, tenant: str, batch_id: str, pack_id: str, sku: str,
               operation: Callable[[p.Pack, bool], T], *,
               test_sqlite: bool = False) -> T:
    """Apply a trusted policy operation; return only after caller commits.

    The callback receives server-owned Wave 2 eligibility. Resource and flow
    grants must also be derived under the documented lock order by the API
    service; they are not yet wired. Never pass arbitrary worker callbacks.
    """
    _transaction(db, test_sqlite=test_sqlite)
    batch = _lock_batch(db, tenant, batch_id)
    row = _lock_pack(db, tenant, batch_id, pack_id)
    p.require(row.garment_sku == sku and pack_id in batch.pack_ids, "pack_scope_mismatch")
    pack, roles = _load(db, row)
    allowed = bool(batch.release_decision and pack_id in batch.release_decision["wave2_eligible"])
    result = operation(pack, allowed)
    _store(row, pack, roles)
    db.flush()
    return result


def release_barrier(db: Session, tenant: str, batch_id: str, now: int, *,
                    test_sqlite: bool = False) -> dict:
    """Persist one monotonic release snapshot; late packs cannot re-close it."""
    _transaction(db, test_sqlite=test_sqlite)
    batch = _lock_batch(db, tenant, batch_id)
    if batch.release_decision is not None:
        return dict(batch.release_decision)
    packs = []
    for pid in sorted(batch.pack_ids):
        row = _lock_pack(db, tenant, batch_id, pid)
        packs.append(_load(db, row)[0])
    decision = p.barrier(p.Batch(batch.batch_id, tenant, tuple(batch.pack_ids),
                                  batch.created_at), packs, now)
    if decision["open"]:
        decision = {**decision, "releasedAt": now}
        batch.release_decision = decision
        db.flush()
    return decision
