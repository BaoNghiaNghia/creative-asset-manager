from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import InventoryDailyRunEventModel, InventoryDailyRunModel, InventoryDocumentModel, InventoryReviewModel, InventoryTransactionModel

INVENTORY_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")
CHECKPOINTS = ("completeness_check", "preclose_check")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DailyRunResult:
    id: str
    business_date: date
    status: str
    ready: bool
    finalized: bool
    forced: bool
    snapshot: dict[str, Any]
    finalized_at: datetime | None
    finalized_by: str | None

    @classmethod
    def from_model(cls, run: InventoryDailyRunModel) -> "DailyRunResult":
        snapshot = dict(run.location_state_json or {})
        return cls(run.id, run.business_date, run.status, bool(snapshot.get("ready")), run.status == "finalized", run.finalized_with_missing, snapshot, run.finalized_at, run.finalized_by)


class DailyRunBlocked(ValueError):
    def __init__(self, snapshot: dict[str, Any]):
        super().__init__("inventory_daily_run_not_ready")
        self.snapshot = snapshot


class InventoryDailyRunService:
    """Inventory-only readiness/finalization; intentionally independent of Creative."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    @staticmethod
    def business_date(now: datetime | None = None) -> date:
        value = now or _utcnow()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(INVENTORY_TIMEZONE).date()

    def get(self, tenant_id: str, business_date: date) -> DailyRunResult | None:
        with self.session_factory() as session:
            run = session.scalar(select(InventoryDailyRunModel).where(InventoryDailyRunModel.tenant_id == tenant_id, InventoryDailyRunModel.business_date == business_date))
            return DailyRunResult.from_model(run) if run else None

    def evaluate(self, tenant_id: str, business_date: date, *, checkpoint: str = "completeness_check", actor_id: str | None = None) -> DailyRunResult:
        if checkpoint not in CHECKPOINTS:
            raise ValueError("invalid_daily_checkpoint")
        with self.session_factory.begin() as session:
            run = self._run(session, tenant_id, business_date)
            if run.status == "finalized":
                return DailyRunResult.from_model(run)
            event_key = f"{business_date.isoformat()}:{checkpoint}"
            existing = session.scalar(select(InventoryDailyRunEventModel).where(InventoryDailyRunEventModel.tenant_id == tenant_id, InventoryDailyRunEventModel.daily_run_id == run.id, InventoryDailyRunEventModel.idempotency_key == event_key))
            if existing is not None:
                return DailyRunResult.from_model(run)
            snapshot = self._snapshot(session, tenant_id, business_date)
            run.location_state_json = snapshot
            run.status = "ready" if snapshot["ready"] else "failed"
            session.add(InventoryDailyRunEventModel(tenant_id=tenant_id, daily_run_id=run.id, idempotency_key=event_key, event_type=checkpoint, actor_id=actor_id, snapshot_json=snapshot))
            session.flush()
            return DailyRunResult.from_model(run)

    def finalize(self, tenant_id: str, business_date: date, *, actor_id: str | None, force: bool = False, reason: str | None = None) -> DailyRunResult:
        if force and not (reason or "").strip():
            raise ValueError("forced_finalize_reason_required")
        with self.session_factory.begin() as session:
            run = self._run(session, tenant_id, business_date)
            if run.status == "finalized":
                return DailyRunResult.from_model(run)
            snapshot = self._snapshot(session, tenant_id, business_date)
            if not snapshot["ready"] and not force:
                run.location_state_json = snapshot
                run.status = "failed"
                session.flush()
                raise DailyRunBlocked(snapshot)
            run.location_state_json = snapshot
            run.status = "finalized"
            run.finalized_by = actor_id
            run.finalized_at = _utcnow()
            run.finalized_with_missing = force
            event_key = f"{business_date.isoformat()}:finalize:{'force' if force else 'normal'}"
            session.add(InventoryDailyRunEventModel(tenant_id=tenant_id, daily_run_id=run.id, idempotency_key=event_key, event_type="forced_finalized" if force else "finalized", actor_id=actor_id, reason=reason.strip() if reason else None, snapshot_json=snapshot))
            session.flush()
            return DailyRunResult.from_model(run)

    @staticmethod
    def _run(session: Session, tenant_id: str, business_date: date) -> InventoryDailyRunModel:
        # Serialize each tenant/day on PostgreSQL.  This avoids a unique-event
        # race between two scheduler processes while retaining SQLite support.
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"inventory-daily:{tenant_id}:{business_date.isoformat()}"})
        query = select(InventoryDailyRunModel).where(InventoryDailyRunModel.tenant_id == tenant_id, InventoryDailyRunModel.business_date == business_date)
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        run = session.scalar(query)
        if run is not None:
            return run
        run = InventoryDailyRunModel(tenant_id=tenant_id, business_date=business_date, idempotency_key=f"daily-run:{business_date.isoformat()}")
        try:
            with session.begin_nested():
                session.add(run)
                session.flush()
            return run
        except IntegrityError:
            run = session.scalar(query)
            if run is None:
                raise
            return run

    @staticmethod
    def _snapshot(session: Session, tenant_id: str, business_date: date) -> dict[str, Any]:
        docs = list(session.scalars(select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == tenant_id, InventoryDocumentModel.business_date == business_date)))
        blockers: list[dict[str, Any]] = []
        if not docs:
            blockers.append({"code": "missing_documents", "count": 0})
        incomplete = [doc.id for doc in docs if doc.received_pages < doc.expected_pages]
        if incomplete:
            blockers.append({"code": "missing_pages", "document_ids": incomplete})
        # A day is only ready when every document reached an outcome that
        # can be committed.  Intermediate stages must never be skipped just
        # because they are not an explicit failure state.
        status_blocked = [doc.id for doc in docs if doc.status not in {"approved", "finalized"}]
        if status_blocked:
            blockers.append({"code": "document_not_ready", "document_ids": status_blocked})
        document_ids = [doc.id for doc in docs]
        if document_ids:
            open_reviews = list(session.scalars(select(InventoryReviewModel.id).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.document_id.in_(document_ids), InventoryReviewModel.status.in_(("pending", "in_review")))))
            if open_reviews:
                blockers.append({"code": "open_reviews", "review_ids": open_reviews})
            approved = [doc.id for doc in docs if doc.status == "approved"]
            if approved:
                committed = set(session.scalars(select(InventoryTransactionModel.source_document_id).where(InventoryTransactionModel.tenant_id == tenant_id, InventoryTransactionModel.source_document_id.in_(approved))))
                missing = sorted(set(approved) - committed)
                if missing:
                    blockers.append({"code": "uncommitted_approved_documents", "document_ids": missing})
            active_jobs = list(session.scalars(select(InventoryJobModel.id).where(InventoryJobModel.tenant_id == tenant_id, InventoryJobModel.entity_id.in_(document_ids), InventoryJobModel.status.in_(("pending", "processing", "retry")))))
            if active_jobs:
                blockers.append({"code": "blocking_jobs", "job_ids": active_jobs})
        statuses = dict(session.execute(select(InventoryDocumentModel.status, func.count(InventoryDocumentModel.id)).where(InventoryDocumentModel.tenant_id == tenant_id, InventoryDocumentModel.business_date == business_date).group_by(InventoryDocumentModel.status)).all())
        return {"business_date": business_date.isoformat(), "ready": not blockers, "blockers": blockers, "document_count": len(docs), "document_statuses": statuses, "evaluated_at": _utcnow().isoformat()}
