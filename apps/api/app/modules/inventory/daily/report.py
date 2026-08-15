from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.persistence_model import (
    InventoryDailyRunModel,
    InventoryDocumentModel,
    InventoryItemModel,
    InventoryLocationModel,
    InventoryReviewModel,
    InventoryTransactionModel,
)


class DailyReportNotFinalized(ValueError):
    pass


class InventoryDailyReportService:
    """Build and persist the immutable JSON report for one finalized Inventory day."""

    schema_version = "inventory-daily-report-v1"

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def get(self, tenant_id: str, business_date: date) -> dict[str, Any] | None:
        with self.session_factory() as session:
            run = session.scalar(select(InventoryDailyRunModel).where(
                InventoryDailyRunModel.tenant_id == tenant_id,
                InventoryDailyRunModel.business_date == business_date,
            ))
            if run is None:
                return None
            return (run.location_state_json or {}).get("daily_report")

    def generate(self, tenant_id: str, business_date: date) -> dict[str, Any]:
        """Generate once after finalization; later calls return the exact snapshot."""
        with self.session_factory.begin() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"inventory-daily-report:{tenant_id}:{business_date.isoformat()}"})
            query = select(InventoryDailyRunModel).where(InventoryDailyRunModel.tenant_id == tenant_id, InventoryDailyRunModel.business_date == business_date)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            run = session.scalar(query)
            if run is None:
                raise LookupError("inventory_daily_run_not_found")
            if run.status != "finalized":
                raise DailyReportNotFinalized("inventory_daily_run_not_finalized")
            state = dict(run.location_state_json or {})
            existing = state.get("daily_report")
            if existing is not None:
                return existing
            report = self._build(session, tenant_id, business_date, run, state)
            state["daily_report"] = report
            run.location_state_json = state
            session.flush()
            return report

    def _build(self, session: Session, tenant_id: str, business_date: date, run: InventoryDailyRunModel, state: dict[str, Any]) -> dict[str, Any]:
        documents = list(session.scalars(select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == tenant_id, InventoryDocumentModel.business_date == business_date)))
        document_ids = [row.id for row in documents]
        reviews = [] if not document_ids else list(session.scalars(select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.document_id.in_(document_ids))))
        transactions = list(session.scalars(select(InventoryTransactionModel).where(InventoryTransactionModel.tenant_id == tenant_id, InventoryTransactionModel.business_date == business_date, InventoryTransactionModel.status == "posted")))
        locations = {row.id: row for row in session.scalars(select(InventoryLocationModel).where(InventoryLocationModel.tenant_id == tenant_id))}
        items = {row.id: row for row in session.scalars(select(InventoryItemModel).where(InventoryItemModel.tenant_id == tenant_id))}
        by_location: dict[str, int] = defaultdict(int)
        for row in documents:
            location = locations.get(row.location_id)
            by_location[location.code if location else "unassigned"] += 1
        report_locations, anomalies = self._location_rows(transactions, locations, items)
        return {
            "schema_version": self.schema_version, "business_date": business_date.isoformat(),
            "daily_run": {"id": run.id, "status": run.status, "finalized_at": run.finalized_at.isoformat() if run.finalized_at else None, "finalized_by": run.finalized_by, "forced": run.finalized_with_missing},
            "readiness": {key: value for key, value in state.items() if key != "daily_report"},
            "documents": {"total": len(documents), "by_status": dict(sorted(self._counts(documents, "status").items())), "by_type": dict(sorted(self._counts(documents, "document_type").items())), "by_location": dict(sorted(by_location.items()))},
            "reviews": {"total": len(reviews), "by_status": dict(sorted(self._counts(reviews, "status").items())), "unresolved": sum(row.status in {"pending", "in_review"} for row in reviews)},
            "transactions": {"total": len(transactions), "by_type": dict(sorted(self._counts(transactions, "transaction_type").items()))},
            "locations": report_locations, "anomalies": anomalies,
        }

    @staticmethod
    def _counts(rows: list[Any], attribute: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[getattr(row, attribute)] += 1
        return counts

    @staticmethod
    def _decimal(value: Decimal) -> str:
        return format(value, "f")

    def _location_rows(self, transactions, locations, items):
        totals: dict[tuple[str | None, str], dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for transaction in transactions:
            totals[(transaction.location_id, transaction.item_id)][transaction.transaction_type] += transaction.quantity_base_unit
        grouped: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        anomalies: list[dict[str, Any]] = []
        ordered = sorted(totals.items(), key=lambda row: ((locations.get(row[0][0]).code if locations.get(row[0][0]) else ""), (items.get(row[0][1]).sku if items.get(row[0][1]) else row[0][1])))
        for (location_id, item_id), values in ordered:
            opening, receipts, transfers_in = values["opening_balance"], values["receipt"], values["transfer_in"]
            transfers_out, closing, waste = values["transfer_out"], values["closing_count"], values["waste"]
            usage_adjustment = values["usage_adjustment"]
            usage = opening + receipts + transfers_in - transfers_out - closing - waste
            location, item = locations.get(location_id), items.get(item_id)
            grouped[location_id].append({"item_id": item_id, "sku": item.sku if item else None, "item_name": item.name if item else None, "base_unit": item.base_unit if item else None, "opening": self._decimal(opening), "receipts": self._decimal(receipts), "transfers_in": self._decimal(transfers_in), "transfers_out": self._decimal(transfers_out), "closing": self._decimal(closing), "waste": self._decimal(waste), "usage": self._decimal(usage), "usage_adjustment": self._decimal(usage_adjustment)})
            if usage < 0:
                anomalies.append({"code": "negative_usage", "location_id": location_id, "item_id": item_id, "usage": self._decimal(usage), "message": "Usage is negative and has been retained without clamping."})
        rows = []
        for location_id, item_rows in sorted(grouped.items(), key=lambda row: (locations.get(row[0]).code if locations.get(row[0]) else "")):
            location = locations.get(location_id)
            rows.append({"location_id": location_id, "location_code": location.code if location else None, "location_name": location.name if location else None, "items": item_rows})
        return rows, anomalies
