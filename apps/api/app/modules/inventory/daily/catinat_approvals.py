"""Tenant-scoped, audited manager review workflow for Catinat (Q17/Q42/Q43/Q44).

Approvals authorize a subsequent independently verified workflow; this
module NEVER writes Google Sheets, applies inventory counts or alters
an immutable signed closing snapshot.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalEventModel,
    InventoryCatinatApprovalRequestModel,
    inventory_utcnow,
)

KINDS = frozenset({
    "discrepancy", "catalog_mapping", "package_conversion",
    "post_close_adjustment", "transfer_exception",
})
DECISIONS = frozenset({"approved", "rejected"})
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


class CatinatApprovalBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _nonblank(value: str, *, minimum: int, maximum: int, code: str) -> str:
    cleaned = str(value or "").strip()
    if not minimum <= len(cleaned) <= maximum:
        raise CatinatApprovalBlocked(code)
    return cleaned


def _fingerprint(
    business_date: date, kind: str, subject_ref: str,
    proposal: dict[str, Any], reason: str,
) -> str:
    try:
        encoded = json.dumps(
            {"date": business_date.isoformat(), "kind": kind,
             "subject": subject_ref, "proposal": proposal, "reason": reason},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CatinatApprovalBlocked("invalid_approval_payload") from exc
    if len(encoded.encode("utf-8")) > 8192:
        raise CatinatApprovalBlocked("approval_payload_too_large")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _view(row: InventoryCatinatApprovalRequestModel, *, replay: bool = False) -> dict:
    return {
        "id": row.id,
        "business_date": row.business_date.isoformat(),
        "kind": row.kind,
        "subject_ref": row.subject_ref,
        "proposal": dict(row.payload_json or {}),
        "proposal_hash": row.proposal_hash,
        "reason": row.reason,
        "status": row.status,
        "requested_by": row.requested_by,
        "decided_by": row.decided_by,
        "decision_reason": row.decision_reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "idempotent_replay": replay,
        "inventory_write_performed": False,
    }


class CatinatApprovalService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def request(
        self, *, tenant_id: str, actor_id: str, idempotency_key: str,
        business_date: date, kind: str, subject_ref: str,
        proposal: dict[str, Any], reason: str,
    ) -> dict:
        tenant = _nonblank(tenant_id, minimum=1, maximum=255, code="tenant_required")
        actor = _nonblank(actor_id, minimum=1, maximum=255, code="actor_required")
        if not _KEY.fullmatch(str(idempotency_key or "")):
            raise CatinatApprovalBlocked("invalid_approval_idempotency_key")
        if kind not in KINDS:
            raise CatinatApprovalBlocked("invalid_approval_kind")
        subject = _nonblank(subject_ref, minimum=1, maximum=255, code="approval_subject_required")
        why = _nonblank(reason, minimum=8, maximum=1000, code="approval_reason_required")
        if not isinstance(proposal, dict) or not proposal:
            raise CatinatApprovalBlocked("approval_proposal_required")
        digest = _fingerprint(business_date, kind, subject, proposal, why)
        with self.sessions() as session:
            try:
                with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        session.execute(
                            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                            {"key": f"catinat-approval:{tenant}:{idempotency_key}"},
                        )
                    row = session.scalar(select(InventoryCatinatApprovalRequestModel).where(
                        InventoryCatinatApprovalRequestModel.tenant_id == tenant,
                        InventoryCatinatApprovalRequestModel.idempotency_key == idempotency_key,
                    ))
                    if row is not None:
                        return self._replay(row, digest, actor)
                    row = InventoryCatinatApprovalRequestModel(
                        tenant_id=tenant, idempotency_key=idempotency_key,
                        business_date=business_date, kind=kind, subject_ref=subject,
                        payload_json=proposal, proposal_hash=digest, reason=why,
                        requested_by=actor, status="pending", created_at=inventory_utcnow(),
                    )
                    session.add(row)
                    session.flush()
                    session.add(InventoryCatinatApprovalEventModel(
                        tenant_id=tenant, request_id=row.id,
                        action="requested", actor_id=actor, note=why,
                        created_at=inventory_utcnow(),
                    ))
                    session.flush()
                    return _view(row)
            except IntegrityError:
                session.rollback()
                row = session.scalar(select(InventoryCatinatApprovalRequestModel).where(
                    InventoryCatinatApprovalRequestModel.tenant_id == tenant,
                    InventoryCatinatApprovalRequestModel.idempotency_key == idempotency_key,
                ))
                if row is None:
                    raise
                return self._replay(row, digest, actor)

    @staticmethod
    def _replay(row: InventoryCatinatApprovalRequestModel, digest: str, actor: str) -> dict:
        if row.proposal_hash != digest or row.requested_by != actor:
            raise CatinatApprovalBlocked("approval_idempotency_conflict")
        return _view(row, replay=True)

    def decide(
        self, *, tenant_id: str, actor_id: str, request_id: str,
        decision: str, reason: str,
    ) -> dict:
        tenant = _nonblank(tenant_id, minimum=1, maximum=255, code="tenant_required")
        actor = _nonblank(actor_id, minimum=1, maximum=255, code="actor_required")
        request = _nonblank(request_id, minimum=1, maximum=36, code="invalid_approval_id")
        if decision not in DECISIONS:
            raise CatinatApprovalBlocked("invalid_approval_decision")
        note = _nonblank(reason, minimum=8, maximum=1000, code="approval_decision_reason_required")
        with self.sessions() as session:
            with session.begin():
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"catinat-decide:{tenant}:{request}"},
                    )
                query = select(InventoryCatinatApprovalRequestModel).where(
                    InventoryCatinatApprovalRequestModel.tenant_id == tenant,
                    InventoryCatinatApprovalRequestModel.id == request,
                )
                if session.bind is not None and session.bind.dialect.name == "postgresql":
                    query = query.with_for_update()
                row = session.scalar(query)
                if row is None:
                    raise CatinatApprovalBlocked("approval_not_found")
                if row.requested_by == actor:
                    raise CatinatApprovalBlocked("self_approval_forbidden")
                if row.status != "pending":
                    if (
                        row.status == decision
                        and row.decided_by == actor
                        and row.decision_reason == note
                    ):
                        return _view(row, replay=True)
                    raise CatinatApprovalBlocked("approval_already_decided")
                row.status = decision
                row.decided_by = actor
                row.decision_reason = note
                row.decided_at = inventory_utcnow()
                session.add(InventoryCatinatApprovalEventModel(
                    tenant_id=tenant, request_id=row.id, action=decision,
                    actor_id=actor, note=note, created_at=inventory_utcnow(),
                ))
                session.flush()
                return _view(row)

    def get(self, *, tenant_id: str, request_id: str) -> dict:
        with self.sessions() as session:
            row = session.scalar(select(InventoryCatinatApprovalRequestModel).where(
                InventoryCatinatApprovalRequestModel.tenant_id == tenant_id,
                InventoryCatinatApprovalRequestModel.id == request_id,
            ))
            if row is None:
                raise CatinatApprovalBlocked("approval_not_found")
            return _view(row)

    def list(self, *, tenant_id: str, status: str | None = None, limit: int = 100) -> list[dict]:
        if status is not None and status not in {"pending", "approved", "rejected"}:
            raise CatinatApprovalBlocked("invalid_approval_status")
        if not 1 <= limit <= 100:
            raise CatinatApprovalBlocked("invalid_approval_limit")
        with self.sessions() as session:
            query = select(InventoryCatinatApprovalRequestModel).where(
                InventoryCatinatApprovalRequestModel.tenant_id == tenant_id
            )
            if status is not None:
                query = query.where(InventoryCatinatApprovalRequestModel.status == status)
            rows = session.scalars(query.order_by(
                InventoryCatinatApprovalRequestModel.created_at.desc(),
                InventoryCatinatApprovalRequestModel.id.desc(),
            ).limit(limit)).all()
            return [_view(row) for row in rows]
