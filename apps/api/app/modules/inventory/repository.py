from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.inventory.persistence_model import (
    InventoryAiAnalysisModel,
    InventoryDailyRunModel,
    InventoryDocumentModel,
    InventoryDocumentPageModel,
    InventoryExportModel,
    InventoryItemAliasModel,
    InventoryItemModel,
    InventoryLineModel,
    InventoryLocationModel,
    InventoryReviewModel,
    InventorySettingsModel,
    inventory_utcnow,
    InventorySourceFileModel,
    InventoryTransactionModel,
)
from app.modules.inventory.schema import (
    InventoryAnalysisInput,
    InventoryDailyRunInput,
    InventoryDocumentInput,
    InventoryDocumentPageInput,
    InventoryExportInput,
    InventoryItemInput,
    InventoryLineInput,
    InventoryReviewInput,
    InventorySettingsInput,
    InventorySourceFileInput,
    InventoryTransactionInput,
)


InventoryModel = TypeVar("InventoryModel")


class InventoryTenantRepository:
    """Tenant-scoped persistence helpers; callers own transactions and commits."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, model: type[InventoryModel], tenant_id: str, record_id: str) -> InventoryModel | None:
        return self.session.scalar(
            select(model).where(model.tenant_id == tenant_id, model.id == record_id)
        )

    def update_fields(
        self,
        model: type[InventoryModel],
        tenant_id: str,
        record_id: str,
        values: dict[str, Any],
    ) -> InventoryModel | None:
        if "tenant_id" in values or "id" in values:
            raise ValueError("tenant_id and id cannot be changed")
        return self.session.scalars(
            update(model)
            .where(model.tenant_id == tenant_id, model.id == record_id)
            .values(**values)
            .returning(model)
            .execution_options(synchronize_session=False)
        ).first()


class InventorySettingsRepository(InventoryTenantRepository):
    def upsert(self, tenant_id: str, value: InventorySettingsInput) -> InventorySettingsModel:
        row = self.session.scalar(
            select(InventorySettingsModel).where(InventorySettingsModel.tenant_id == tenant_id)
        )
        values = value.model_dump()
        if row is None:
            row = InventorySettingsModel(tenant_id=tenant_id, **values)
            self.session.add(row)
        else:
            for key, field_value in values.items():
                setattr(row, key, field_value)
        self.session.flush()
        return row


class InventorySourceFileRepository(InventoryTenantRepository):
    def register(
        self, tenant_id: str, value: InventorySourceFileInput
    ) -> InventorySourceFileModel:
        return self.register_with_result(tenant_id, value)[0]

    def register_with_result(
        self, tenant_id: str, value: InventorySourceFileInput, *, status: str = "discovered"
    ) -> tuple[InventorySourceFileModel, bool]:
        identity = (
            InventorySourceFileModel.tenant_id == tenant_id,
            InventorySourceFileModel.external_source_id == value.external_source_id,
            InventorySourceFileModel.drive_file_id == value.drive_file_id,
            InventorySourceFileModel.drive_modified_time == value.drive_modified_time,
        )
        existing = self.session.scalar(select(InventorySourceFileModel).where(*identity))
        if existing is not None:
            existing.last_seen_at = inventory_utcnow()
            return existing, False
        try:
            with self.session.begin_nested():
                row = InventorySourceFileModel(
                    tenant_id=tenant_id,
                    status=status,
                    **value.model_dump(),
                )
                self.session.add(row)
                self.session.flush()
            return row, True
        except IntegrityError:
            existing = self.session.scalar(select(InventorySourceFileModel).where(*identity))
            if existing is None:
                raise
            return existing, False

    def find_by_content_hash(
        self, tenant_id: str, content_sha256: str
    ) -> tuple[InventorySourceFileModel, ...]:
        return tuple(
            self.session.scalars(
                select(InventorySourceFileModel)
                .where(
                    InventorySourceFileModel.tenant_id == tenant_id,
                    InventorySourceFileModel.content_sha256 == content_sha256,
                )
                .order_by(InventorySourceFileModel.created_at)
            )
        )


class InventoryCatalogRepository(InventoryTenantRepository):
    def create_location(self, tenant_id: str, *, code: str, name: str) -> InventoryLocationModel:
        existing = self.session.scalar(
            select(InventoryLocationModel).where(
                InventoryLocationModel.tenant_id == tenant_id,
                InventoryLocationModel.code == code,
            )
        )
        if existing is not None:
            return existing
        row = InventoryLocationModel(tenant_id=tenant_id, code=code, name=name)
        self.session.add(row)
        self.session.flush()
        return row

    def create_item(self, tenant_id: str, value: InventoryItemInput) -> InventoryItemModel:
        existing = self.session.scalar(
            select(InventoryItemModel).where(
                InventoryItemModel.tenant_id == tenant_id,
                InventoryItemModel.sku == value.sku,
            )
        )
        if existing is not None:
            return existing
        row = InventoryItemModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def add_alias(
        self, tenant_id: str, *, item_id: str, alias: str, normalized_alias: str
    ) -> InventoryItemAliasModel:
        if self.get(InventoryItemModel, tenant_id, item_id) is None:
            raise LookupError("Inventory item not found in tenant")
        existing = self.session.scalar(
            select(InventoryItemAliasModel).where(
                InventoryItemAliasModel.tenant_id == tenant_id,
                InventoryItemAliasModel.normalized_alias == normalized_alias,
            )
        )
        if existing is not None:
            if existing.item_id != item_id:
                raise ValueError("Alias already belongs to another Inventory item")
            return existing
        row = InventoryItemAliasModel(
            tenant_id=tenant_id,
            item_id=item_id,
            alias=alias,
            normalized_alias=normalized_alias,
        )
        self.session.add(row)
        self.session.flush()
        return row


class InventoryDocumentRepository(InventoryTenantRepository):
    def create_document(
        self, tenant_id: str, value: InventoryDocumentInput
    ) -> InventoryDocumentModel:
        existing = self._by_key(InventoryDocumentModel, tenant_id, value.idempotency_key)
        if existing is not None:
            return existing
        if self.get(InventoryLocationModel, tenant_id, value.location_id) is None:
            raise LookupError("Inventory location not found in tenant")
        row = InventoryDocumentModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def add_page(
        self, tenant_id: str, value: InventoryDocumentPageInput
    ) -> InventoryDocumentPageModel:
        if self.get(InventoryDocumentModel, tenant_id, value.document_id) is None:
            raise LookupError("Inventory document not found in tenant")
        if self.get(InventorySourceFileModel, tenant_id, value.source_file_id) is None:
            raise LookupError("Inventory source file not found in tenant")
        existing = self.session.scalar(
            select(InventoryDocumentPageModel).where(
                InventoryDocumentPageModel.tenant_id == tenant_id,
                InventoryDocumentPageModel.source_file_id == value.source_file_id,
            )
        )
        if existing is not None:
            return existing
        row = InventoryDocumentPageModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def record_analysis(
        self, tenant_id: str, value: InventoryAnalysisInput
    ) -> InventoryAiAnalysisModel:
        existing = self._by_key(InventoryAiAnalysisModel, tenant_id, value.idempotency_key)
        if existing is not None:
            return existing
        if self.get(InventoryDocumentPageModel, tenant_id, value.page_id) is None:
            raise LookupError("Inventory document page not found in tenant")
        row = InventoryAiAnalysisModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def add_line(
        self, tenant_id: str, value: InventoryLineInput
    ) -> InventoryLineModel:
        if self.get(InventoryDocumentModel, tenant_id, value.document_id) is None:
            raise LookupError("Inventory document not found in tenant")
        if self.get(InventoryDocumentPageModel, tenant_id, value.page_id) is None:
            raise LookupError("Inventory document page not found in tenant")
        if self.get(InventoryAiAnalysisModel, tenant_id, value.analysis_id) is None:
            raise LookupError("Inventory analysis not found in tenant")
        if value.item_id is not None and self.get(
            InventoryItemModel, tenant_id, value.item_id
        ) is None:
            raise LookupError("Inventory item not found in tenant")
        existing = self.session.scalar(
            select(InventoryLineModel).where(
                InventoryLineModel.tenant_id == tenant_id,
                InventoryLineModel.analysis_id == value.analysis_id,
                InventoryLineModel.line_number == value.line_number,
            )
        )
        if existing is not None:
            return existing
        row = InventoryLineModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def _by_key(self, model: type[InventoryModel], tenant_id: str, key: str):
        return self.session.scalar(
            select(model).where(model.tenant_id == tenant_id, model.idempotency_key == key)
        )


class InventoryReviewRepository(InventoryTenantRepository):
    def create(
        self, tenant_id: str, value: InventoryReviewInput
    ) -> InventoryReviewModel:
        existing = self.session.scalar(
            select(InventoryReviewModel).where(
                InventoryReviewModel.tenant_id == tenant_id,
                InventoryReviewModel.idempotency_key == value.idempotency_key,
            )
        )
        if existing is not None:
            return existing
        if self.get(InventoryDocumentModel, tenant_id, value.document_id) is None:
            raise LookupError("Inventory document not found in tenant")
        if value.line_id is not None and self.get(
            InventoryLineModel, tenant_id, value.line_id
        ) is None:
            raise LookupError("Inventory line not found in tenant")
        row = InventoryReviewModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row


class InventoryLedgerRepository(InventoryTenantRepository):
    def append(
        self, tenant_id: str, value: InventoryTransactionInput
    ) -> InventoryTransactionModel:
        existing = self.session.scalar(select(InventoryTransactionModel).where(
            InventoryTransactionModel.tenant_id == tenant_id,
            InventoryTransactionModel.idempotency_key == value.idempotency_key,
        ))
        if existing is not None:
            return existing
        for model, record_id, label in (
            (InventoryLocationModel, value.location_id, "location"),
            (InventoryItemModel, value.item_id, "item"),
            (InventoryDocumentModel, value.source_document_id, "document"),
        ):
            if self.get(model, tenant_id, record_id) is None:
                raise LookupError(f"Inventory {label} not found in tenant")
        row = InventoryTransactionModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def history(
        self, tenant_id: str, *, location_id: str, item_id: str
    ) -> tuple[InventoryTransactionModel, ...]:
        return tuple(self.session.scalars(
            select(InventoryTransactionModel).where(
                InventoryTransactionModel.tenant_id == tenant_id,
                InventoryTransactionModel.location_id == location_id,
                InventoryTransactionModel.item_id == item_id,
            ).order_by(
                InventoryTransactionModel.business_date,
                InventoryTransactionModel.created_at,
                InventoryTransactionModel.id,
            )
        ))


class InventoryDailyRepository(InventoryTenantRepository):
    def get_or_create_run(
        self, tenant_id: str, value: InventoryDailyRunInput
    ) -> InventoryDailyRunModel:
        existing = self.session.scalar(select(InventoryDailyRunModel).where(
            InventoryDailyRunModel.tenant_id == tenant_id,
            InventoryDailyRunModel.business_date == value.business_date,
        ))
        if existing is not None:
            return existing
        row = InventoryDailyRunModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

    def create_export(
        self, tenant_id: str, value: InventoryExportInput
    ) -> InventoryExportModel:
        existing = self.session.scalar(select(InventoryExportModel).where(
            InventoryExportModel.tenant_id == tenant_id,
            InventoryExportModel.idempotency_key == value.idempotency_key,
        ))
        if existing is not None:
            return existing
        if self.get(InventoryDailyRunModel, tenant_id, value.daily_run_id) is None:
            raise LookupError("Inventory daily run not found in tenant")
        row = InventoryExportModel(tenant_id=tenant_id, **value.model_dump())
        self.session.add(row)
        self.session.flush()
        return row

