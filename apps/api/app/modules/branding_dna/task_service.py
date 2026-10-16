"""BD-01 scanner task service.

Tasks may be staged by a curator. There is deliberately NO public transition
from staged -> pending and NO HTTP worker delivery of jobs. All execution paths
are internal primitives for an eventual separately authorized source adapter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel, now_utc
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.task_model import BrandScanTaskModel

LEASE_SECONDS = 120
MAX_STAGED_PER_SHOP = 500
RETRY_ERROR_CODES = frozenset({
    "temporary_network_error", "rate_limited", "source_unavailable", "parser_changed",
    "manual_check_required",
})


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _task_dict(task: BrandScanTaskModel) -> dict:
    return {
        "id": task.id, "shop_id": task.shop_id, "snapshot_id": task.snapshot_id,
        "task_kind": task.task_kind, "status": task.status,
        "attempts": task.attempts, "max_attempts": task.max_attempts,
        "checkpoint_seq": task.checkpoint_seq,
        "lease_generation": task.lease_generation,
        "last_error_code": task.last_error_code,
        "created_at": task.created_at.isoformat(), "updated_at": task.updated_at.isoformat(),
        "retry_after": task.retry_after.isoformat() if task.retry_after else None,
    }


def stage_from_snapshot(session: Session, *, tenant_id: str, shop_id: str,
                        snapshot_id: str, actor_id: str) -> tuple[BrandScanTaskModel, bool]:
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.tenant_id == tenant_id, BrandShopModel.id == shop_id,
    ))
    if shop is None:
        raise HTTPException(404, detail="Shop not found")
    if shop.access_mode not in ("owned", "authorized"):
        raise HTTPException(403, detail="Authorized or owned shop required")
    snapshot = session.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.id == snapshot_id,
        BrandListingSnapshotModel.tenant_id == tenant_id,
        BrandListingSnapshotModel.shop_id == shop_id,
    ))
    if snapshot is None:
        raise HTTPException(404, detail="Listing evidence not found")

    identity = (
        BrandScanTaskModel.tenant_id == tenant_id,
        BrandScanTaskModel.shop_id == shop_id,
        BrandScanTaskModel.snapshot_id == snapshot_id,
        BrandScanTaskModel.task_kind == "verify_listing_evidence",
    )
    existing = session.scalar(select(BrandScanTaskModel).where(*identity))
    if existing:
        return existing, True
    active_count = session.scalar(select(func.count()).select_from(BrandScanTaskModel).where(
        BrandScanTaskModel.tenant_id == tenant_id,
        BrandScanTaskModel.shop_id == shop_id,
        BrandScanTaskModel.status.in_(("staged", "pending", "leased", "retry")),
    )) or 0
    if active_count >= MAX_STAGED_PER_SHOP:
        raise HTTPException(429, detail="Per-shop staged task quota reached")
    task = BrandScanTaskModel(
        tenant_id=tenant_id, shop_id=shop_id, snapshot_id=snapshot_id, created_by=actor_id,
        status="staged", attempts=0, lease_generation=0, checkpoint_seq=0,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        replay = session.scalar(select(BrandScanTaskModel).where(*identity))
        if replay:
            return replay, True
        raise HTTPException(409, detail="Task staging conflict") from exc
    session.refresh(task)
    return task, False


def recover_expired_leases(session: Session, *, tenant_id: str,
                           now: datetime | None = None, limit: int = 100) -> int:
    """Reclaim expired leases before dispatch. Exhausted tasks are blocked."""
    clock = _aware(now) or now_utc()
    expired = session.scalars(
        select(BrandScanTaskModel).where(
            BrandScanTaskModel.tenant_id == tenant_id,
            BrandScanTaskModel.status == "leased",
            BrandScanTaskModel.lease_until <= clock,
        ).order_by(BrandScanTaskModel.lease_until)
        .with_for_update(skip_locked=True).limit(limit)
    ).all()
    for task in expired:
        task.last_error_code = "lease_expired"
        task.lease_id = None
        task.agent_id = None
        task.lease_until = None
        if task.attempts >= task.max_attempts:
            task.status = "blocked"
            task.retry_after = None
        else:
            task.status = "retry"
            task.retry_after = clock + timedelta(seconds=min(
                3600, 30 * 2 ** max(0, task.attempts - 1),
            ))
    if expired:
        session.commit()
    return len(expired)


def claim_next_task(session: Session, *, tenant_id: str, agent_id: str,
                    dispatch_authorized: bool = False, adapter_verified: bool = False,
                    now: datetime | None = None) -> dict | None:
    """Internal adapter-only CAS entrypoint. Off by default, not called by /lease.

    Even a provisioned worker cannot poll work until a future authorized adapter
    explicitly invokes this function after its separate rights/policy gates.
    """
    if not (dispatch_authorized and adapter_verified):
        return None
    agent = session.scalar(select(BrandWorkerModel).where(
        BrandWorkerModel.id == agent_id, BrandWorkerModel.tenant_id == tenant_id,
        BrandWorkerModel.enabled.is_(True),
    ))
    if agent is None:
        raise HTTPException(403, detail="Worker disabled or tenant mismatch")
    clock = _aware(now) or now_utc()
    recover_expired_leases(session, tenant_id=tenant_id, now=clock)
    task = session.scalar(
        select(BrandScanTaskModel)
        .where(
            BrandScanTaskModel.tenant_id == tenant_id,
            BrandScanTaskModel.attempts < BrandScanTaskModel.max_attempts,
            or_(
                BrandScanTaskModel.status == "pending",
                (BrandScanTaskModel.status == "retry") & (
                    (BrandScanTaskModel.retry_after.is_(None)) |
                    (BrandScanTaskModel.retry_after <= clock)),
            ),
        )
        .order_by(BrandScanTaskModel.created_at, BrandScanTaskModel.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if task is None:
        return None
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.id == task.shop_id,
        BrandShopModel.tenant_id == tenant_id,
        BrandShopModel.access_mode.in_(("owned", "authorized")),
    ))
    if shop is None:
        return None
    task.status = "leased"
    task.attempts += 1
    task.lease_generation += 1
    task.lease_id = str(uuid4())
    task.agent_id = agent_id
    task.lease_until = clock + timedelta(seconds=LEASE_SECONDS)
    task.retry_after = None
    session.commit()
    return {
        "task_id": task.id, "shop_id": task.shop_id, "snapshot_id": task.snapshot_id,
        "task_kind": task.task_kind, "attempt": task.attempts,
        "lease_id": task.lease_id, "lease_generation": task.lease_generation,
        "lease_until": task.lease_until.isoformat(),
        "checkpoint_seq": task.checkpoint_seq,
        # Never return arbitrary external URLs or credentials.
    }


def _fenced_task(session: Session, *, tenant_id: str, task_id: str, agent_id: str,
                 lease_id: str, lease_generation: int, now: datetime) -> BrandScanTaskModel:
    task = session.scalar(select(BrandScanTaskModel).where(
        BrandScanTaskModel.tenant_id == tenant_id, BrandScanTaskModel.id == task_id,
    ).with_for_update())
    if (task is None or task.status != "leased" or task.agent_id != agent_id
            or task.lease_id != lease_id or task.lease_generation != lease_generation
            or _aware(task.lease_until) is None or _aware(task.lease_until) <= now):
        raise HTTPException(409, detail="Stale or expired lease")
    agent_ok = session.scalar(select(BrandWorkerModel.id).where(
        BrandWorkerModel.id == agent_id, BrandWorkerModel.tenant_id == tenant_id,
        BrandWorkerModel.enabled.is_(True),
    ))
    shop_ok = session.scalar(select(BrandShopModel.id).where(
        BrandShopModel.id == task.shop_id, BrandShopModel.tenant_id == tenant_id,
        BrandShopModel.access_mode.in_(("owned", "authorized")),
    ))
    if agent_ok is None or shop_ok is None:
        raise HTTPException(409, detail="Lease authorization revoked")
    return task


def checkpoint_task(session: Session, *, tenant_id: str, task_id: str, agent_id: str,
                    lease_id: str, lease_generation: int, checkpoint_seq: int,
                    now: datetime | None = None) -> dict:
    clock = _aware(now) or now_utc()
    task = _fenced_task(session, tenant_id=tenant_id, task_id=task_id,
                        agent_id=agent_id, lease_id=lease_id,
                        lease_generation=lease_generation, now=clock)
    if checkpoint_seq < task.checkpoint_seq or checkpoint_seq < 0:
        raise HTTPException(409, detail="Checkpoint sequence cannot decrease")
    if checkpoint_seq > task.checkpoint_seq:
        task.checkpoint_seq = checkpoint_seq
        task.lease_until = clock + timedelta(seconds=LEASE_SECONDS)
        session.commit()
    # Replayed ACK does not extend lease or create progress.
    return {"task_id": task.id, "checkpoint_seq": task.checkpoint_seq,
            "lease_generation": task.lease_generation}


def release_for_retry(session: Session, *, tenant_id: str, task_id: str,
                      agent_id: str, lease_id: str, lease_generation: int,
                      error_code: str, now: datetime | None = None) -> dict:
    clock = _aware(now) or now_utc()
    if error_code not in RETRY_ERROR_CODES:
        raise HTTPException(422, detail="Invalid safe error code")
    task = _fenced_task(session, tenant_id=tenant_id, task_id=task_id,
                        agent_id=agent_id, lease_id=lease_id,
                        lease_generation=lease_generation, now=clock)
    task.last_error_code = error_code
    task.lease_id = None
    task.agent_id = None
    task.lease_until = None
    if error_code in {"manual_check_required", "parser_changed"}:
        task.status = "blocked"
        task.retry_after = None
    elif task.attempts >= task.max_attempts:
        task.status = "blocked"
        task.retry_after = None
    else:
        task.status = "retry"
        seconds = min(3600, 30 * 2 ** (task.attempts - 1))
        task.retry_after = clock + timedelta(seconds=seconds)
    session.commit()
    return {"task_id": task.id, "status": task.status,
            "retry_after": task.retry_after.isoformat() if task.retry_after else None}
