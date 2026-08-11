from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.modules.inventory.documents.service import InventoryBusinessFailure
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.persistence_model import (
    InventoryDocumentModel, InventoryLineModel, InventoryTransactionModel,
)

INVENTORY_DOCUMENT_COMMIT_JOB = "inventory_document_commit"


class InventoryDocumentCommitter:
    """Append immutable, tenant-scoped ledger rows for one approved document."""

    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def execute(self, job: InventoryJobModel) -> None:
        document_id = str((job.payload_json or {}).get("document_id") or job.entity_id)
        with self.session_factory() as session:
            query = select(InventoryDocumentModel).where(
                InventoryDocumentModel.tenant_id == job.tenant_id,
                InventoryDocumentModel.id == document_id,
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                query = query.with_for_update()
            document = session.scalar(query)
            if document is None:
                raise InventoryBusinessFailure("inventory_commit_document_not_found", retryable=False)
            if document.status != "approved":
                raise InventoryBusinessFailure("inventory_commit_document_not_approved", retryable=False)
            lines = list(session.scalars(select(InventoryLineModel).where(
                InventoryLineModel.tenant_id == job.tenant_id,
                InventoryLineModel.document_id == document.id,
            ).order_by(InventoryLineModel.line_number)))
            expected = self._expected(document, lines)
            for value in expected:
                existing = session.scalar(select(InventoryTransactionModel).where(
                    InventoryTransactionModel.tenant_id == job.tenant_id,
                    InventoryTransactionModel.idempotency_key == value["idempotency_key"],
                ))
                if existing is not None:
                    if not self._matches(existing, value):
                        raise InventoryBusinessFailure("inventory_commit_conflicting_ledger_state", retryable=False)
                    continue
                session.add(InventoryTransactionModel(**value))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # A concurrent committer won; validate its immutable rows.
                with self.session_factory() as verify:
                    for value in expected:
                        existing = verify.scalar(select(InventoryTransactionModel).where(
                            InventoryTransactionModel.tenant_id == job.tenant_id,
                            InventoryTransactionModel.idempotency_key == value["idempotency_key"],
                        ))
                        if existing is None or not self._matches(existing, value):
                            raise InventoryBusinessFailure("inventory_commit_conflicting_ledger_state", retryable=False)

    @staticmethod
    def _matches(row: InventoryTransactionModel, value: dict) -> bool:
        return all(getattr(row, key) == value[key] for key in (
            "transaction_type", "quantity_base_unit", "location_id", "item_id",
            "source_document_id", "source_line_id", "business_date",
            "base_unit_snapshot", "conversion_factor_snapshot",
        ))

    def _expected(self, document: InventoryDocumentModel, lines: list[InventoryLineModel]) -> list[dict]:
        kind = {"opening": "opening_balance", "receipt": "receipt", "stock_count": "closing_count", "waste": "waste"}.get(document.document_type)
        if document.document_type == "warehouse_transfer":
            if not document.location_id or not document.destination_location_id or document.location_id == document.destination_location_id:
                raise InventoryBusinessFailure("inventory_commit_transfer_locations_required", retryable=False)
            return self._transfer_expected(document, lines)
        if kind is None:
            raise InventoryBusinessFailure("inventory_commit_document_type_unsupported", retryable=False)
        values = []
        for line in lines:
            if line.item_id is None or line.quantity_base_unit is None or line.quantity_base_unit < 0:
                raise InventoryBusinessFailure("inventory_commit_line_not_validated", retryable=False)
            if kind == "waste" and (not line.waste_quantity or not line.waste_reason):
                raise InventoryBusinessFailure("inventory_commit_waste_reason_required", retryable=False)
            quantity = line.waste_quantity if kind == "waste" else line.quantity_base_unit
            values.append({
                "tenant_id": document.tenant_id,
                "idempotency_key": f"inventory-transaction:v1:{document.id}:{line.id}:{kind}",
                "business_date": document.business_date,
                "location_id": document.location_id,
                "item_id": line.item_id,
                "transaction_type": kind,
                "quantity_base_unit": quantity,
                "base_unit_snapshot": line.whole_unit or "unit",
                "conversion_factor_snapshot": line.conversion_factor_snapshot or 1,
                "source_document_id": document.id,
                "source_line_id": line.id,
                "metadata_json": {"waste_reason": line.waste_reason} if kind == "waste" else {},
            })
        return values
    def _transfer_expected(self, document, lines):
        values=[]
        for line in lines:
            if line.item_id is None or line.quantity_base_unit is None or line.quantity_base_unit < 0:
                raise InventoryBusinessFailure("inventory_commit_line_not_validated", retryable=False)
            for kind, location, other in (("transfer_out", document.location_id, document.destination_location_id), ("transfer_in", document.destination_location_id, document.location_id)):
                values.append({"tenant_id":document.tenant_id,"idempotency_key":f"inventory-transaction:v1:{document.id}:{line.id}:{kind}","business_date":document.business_date,"location_id":location,"item_id":line.item_id,"transaction_type":kind,"quantity_base_unit":line.quantity_base_unit,"base_unit_snapshot":line.whole_unit or "unit","conversion_factor_snapshot":line.conversion_factor_snapshot or 1,"source_document_id":document.id,"source_line_id":line.id,"metadata_json":{"transfer_identity":document.id,"counterparty_location_id":other}})
        return values
