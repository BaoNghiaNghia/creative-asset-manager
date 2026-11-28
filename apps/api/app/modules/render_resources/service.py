"""P1 capacity-one resource fence. The video adapter is not connected yet.

Do not expose integration_verified or idle proof as worker-controlled input.
No automatic steal on expiration. All calls use the existing CAM DB transaction.
"""
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from .model import RenderResource

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TTL = 3600


class ResourceConflict(ValueError):
    pass


def _require(ok, code):
    if not ok:
        raise ResourceConflict(code)


def _lock(db: Session, tenant: str, resource_id: str, test_sqlite: bool):
    _require(db.in_transaction(), "transaction_required")
    dialect = db.get_bind().dialect.name
    _require(dialect == "postgresql" or (test_sqlite and dialect == "sqlite"),
             "postgresql_locking_required")
    r = db.scalar(select(RenderResource).where(
        RenderResource.tenant_id == tenant,
        RenderResource.resource_id == resource_id).with_for_update().execution_options(populate_existing=True))
    _require(r is not None, "resource_unknown")
    return r


def reserve(db: Session, tenant: str, resource_id: str, flow: str, worker: str,
            operation: str, now: int, *, test_sqlite: bool = False) -> int:
    _require(flow in ("image", "video") and all(isinstance(v, str) and _ID.fullmatch(v)
             for v in (worker, operation)), "resource_identity_invalid")
    r = _lock(db, tenant, resource_id, test_sqlite)
    _require(r.integration_verified, "resource_integration_unverified")
    _require(r.owner_flow is None, "resource_reconciliation_required"
             if r.expires_at is not None and r.expires_at <= now else "resource_busy")
    r.epoch += 1
    r.owner_flow, r.owner_worker, r.owner_operation = flow, worker, operation
    r.expires_at = now + TTL
    db.flush()
    return r.epoch


def _owned(r, flow, worker, operation, epoch):
    _require((r.owner_flow, r.owner_worker, r.owner_operation, r.epoch) ==
             (flow, worker, operation, epoch), "resource_owner_mismatch")
    _require(flow in ("image", "video"), "resource_owner_mismatch")


def renew(db: Session, tenant: str, resource_id: str, flow: str, worker: str,
          operation: str, epoch: int, now: int, *, test_sqlite: bool = False) -> int:
    r = _lock(db, tenant, resource_id, test_sqlite)
    _owned(r, flow, worker, operation, epoch)
    _require(r.expires_at is not None and r.expires_at > now, "resource_lease_expired")
    r.expires_at = max(r.expires_at, now + TTL)
    db.flush()
    return r.expires_at


def release(db: Session, tenant: str, resource_id: str, flow: str, worker: str,
            operation: str, epoch: int, *, verified_idle: bool = False,
            test_sqlite: bool = False):
    """Trusted completion/recovery path; no raw worker flag may prove idle."""
    r = _lock(db, tenant, resource_id, test_sqlite)
    _owned(r, flow, worker, operation, epoch)
    _require(verified_idle is True, "resource_reconciliation_required")
    r.owner_flow = r.owner_worker = r.owner_operation = None
    r.expires_at = None
    db.flush()
