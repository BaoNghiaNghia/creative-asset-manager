from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SourceFileStatus = Literal[
    "discovered", "downloaded", "processing", "processed", "ignored", "failed"
]
DocumentType = Literal["stock_count", "warehouse_transfer", "waste"]
DocumentStatus = Literal[
    "collecting", "analyzing", "needs_review", "approved", "rejected", "finalized"
]
ValidationStatus = Literal[
    "unvalidated", "valid", "invalid", "needs_review", "corrected"
]
ReviewStatus = Literal["pending", "in_review", "approved", "rejected", "cancelled"]
TransactionType = Literal[
    "opening_balance", "receipt", "transfer_out", "transfer_in",
    "closing_count", "waste", "usage_adjustment",
]


class InventorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InventorySettingsInput(InventorySchema):
    external_source_id: str
    inbox_folder_id: str
    processed_folder_id: str | None = None
    reupload_folder_id: str | None = None
    excel_folder_id: str | None = None
    backup_folder_id: str | None = None
    excel_template_file_id: str | None = None
    timezone: str = "Asia/Ho_Chi_Minh"
    auto_approve_confidence: Decimal = Field(default=Decimal("0.95"), ge=0, le=1)
    review_confidence: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)
    drive_poll_interval_seconds: int = Field(default=60, ge=60, le=300)
    enabled: bool = False
    archive_enabled: bool = False
    excel_export_enabled: bool = False


class InventorySourceFileInput(InventorySchema):
    external_source_id: str
    drive_file_id: str
    filename: str
    mime_type: str | None = None
    drive_modified_time: datetime
    drive_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    provider_metadata_json: dict = Field(default_factory=dict)


class InventoryItemInput(InventorySchema):
    sku: str
    name: str
    base_unit: str
    whole_unit: str | None = None
    fraction_unit: str | None = None
    conversion_factor: Decimal = Field(default=Decimal("1"), gt=0)
    category: str | None = None


class InventoryDocumentInput(InventorySchema):
    idempotency_key: str
    business_date: date
    document_type: DocumentType
    location_id: str
    expected_pages: int = Field(default=0, ge=0)
    submitted_by: str | None = None


class InventoryDocumentPageInput(InventorySchema):
    document_id: str
    source_file_id: str
    drive_file_id: str
    page_number: int = Field(gt=0)
    page_count: int = Field(default=1, gt=0)
    content_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    duplicate_of_page_id: str | None = None


class InventoryAnalysisInput(InventorySchema):
    document_id: str
    page_id: str
    analysis_version: int = Field(gt=0)
    idempotency_key: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str


class InventoryLineInput(InventorySchema):
    document_id: str
    page_id: str
    analysis_id: str
    line_number: int = Field(gt=0)
    raw_item_name: str
    item_id: str | None = None
    raw_values_json: dict = Field(default_factory=dict)
    normalized_values_json: dict = Field(default_factory=dict)
    whole_quantity: Decimal | None = None
    fraction_quantity: Decimal | None = None
    quantity_base_unit: Decimal | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)


class InventoryReviewInput(InventorySchema):
    document_id: str
    line_id: str | None = None
    idempotency_key: str
    reason_code: str
    original_value_json: dict = Field(default_factory=dict)
    suggested_value_json: dict = Field(default_factory=dict)


class InventoryTransactionInput(InventorySchema):
    idempotency_key: str
    business_date: date
    location_id: str
    item_id: str
    transaction_type: TransactionType
    quantity_base_unit: Decimal
    base_unit_snapshot: str
    conversion_factor_snapshot: Decimal = Field(gt=0)
    source_document_id: str
    source_line_id: str | None = None
    actor_id: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class InventoryDailyRunInput(InventorySchema):
    business_date: date
    idempotency_key: str


class InventoryExportInput(InventorySchema):
    daily_run_id: str
    idempotency_key: str
    export_version: int = Field(gt=0)
    export_format: Literal["xlsx", "xlsm", "csv"] = "xlsx"
    period_month: str = Field(pattern=r"^\d{4}-\d{2}$")
    business_date: date
    created_by: str | None = None
