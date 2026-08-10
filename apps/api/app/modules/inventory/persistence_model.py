from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_inventory_id() -> str:
    return str(uuid4())


def inventory_utcnow() -> datetime:
    return datetime.now(timezone.utc)


TENANT_ID = String(255)
ENTITY_ID = String(36)
QUANTITY = Numeric(24, 8)
CONFIDENCE = Numeric(7, 6)


class InventorySettingsModel(Base):
    __tablename__ = "inventory_settings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="RESTRICT",
            name="fk_inventory_settings_tenant_source",
        ),
        UniqueConstraint("tenant_id", name="uq_inventory_settings_tenant"),
        CheckConstraint(
            "auto_approve_confidence >= 0 AND auto_approve_confidence <= 1",
            name="ck_inventory_settings_auto_confidence",
        ),
        CheckConstraint(
            "review_confidence >= 0 AND review_confidence <= 1",
            name="ck_inventory_settings_review_confidence",
        ),
        CheckConstraint(
            "review_confidence <= auto_approve_confidence",
            name="ck_inventory_settings_confidence_order",
        ),
        CheckConstraint(
            "drive_poll_interval_seconds >= 60 AND drive_poll_interval_seconds <= 300",
            name="ck_inventory_settings_poll_interval",
        ),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    inbox_folder_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    processed_folder_id: Mapped[str | None] = mapped_column(String(2048))
    reupload_folder_id: Mapped[str | None] = mapped_column(String(2048))
    excel_folder_id: Mapped[str | None] = mapped_column(String(2048))
    backup_folder_id: Mapped[str | None] = mapped_column(String(2048))
    excel_template_file_id: Mapped[str | None] = mapped_column(String(2048))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Ho_Chi_Minh")
    auto_approve_confidence: Mapped[Decimal] = mapped_column(
        CONFIDENCE, nullable=False, default=Decimal("0.950000")
    )
    review_confidence: Mapped[Decimal] = mapped_column(
        CONFIDENCE, nullable=False, default=Decimal("0.700000")
    )
    drive_poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    archive_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    excel_export_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_successful_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_error_code: Mapped[str | None] = mapped_column(String(100))
    last_poll_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=inventory_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow, onupdate=inventory_utcnow
    )


class InventorySourceFileModel(Base):
    __tablename__ = "inventory_source_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "external_source_id"],
            ["external_sources.tenant_id", "external_sources.id"],
            ondelete="RESTRICT",
            name="fk_inventory_source_files_tenant_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "duplicate_of_source_file_id"],
            ["inventory_source_files.tenant_id", "inventory_source_files.id"],
            ondelete="RESTRICT",
            name="fk_inventory_source_files_tenant_duplicate",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_source_files_tenant_id"),
        UniqueConstraint(
            "tenant_id", "external_source_id", "drive_file_id", "drive_modified_time",
            name="uq_inventory_source_files_provider_version",
        ),
        CheckConstraint(
            "status IN ('discovered','queued','downloading','downloaded','duplicate','unsupported','retryable_failure','terminal_failure')",
            name="ck_inventory_source_files_status",
        ),
        CheckConstraint("drive_size IS NULL OR drive_size >= 0", name="ck_inventory_source_files_size"),
        Index("ix_inventory_source_files_tenant_status", "tenant_id", "status", "updated_at"),
        Index("ix_inventory_source_files_content_hash", "tenant_id", "content_sha256"),
        Index("ix_inventory_source_files_drive_identity", "tenant_id", "external_source_id", "drive_file_id"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    external_source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    drive_file_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    drive_modified_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    drive_size: Mapped[int | None] = mapped_column(BigInteger)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    duplicate_of_source_file_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="discovered")
    provider_metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=inventory_utcnow)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preparation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="not_requested"
    )
    preparation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preparation_error_code: Mapped[str | None] = mapped_column(String(100))
    preparation_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=inventory_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow, onupdate=inventory_utcnow
    )


class InventoryLocationModel(Base):
    __tablename__ = "inventory_locations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inventory_locations_tenant_id"),
        UniqueConstraint("tenant_id", "code", name="uq_inventory_locations_tenant_code"),
        Index("ix_inventory_locations_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=inventory_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow, onupdate=inventory_utcnow
    )


class InventoryItemModel(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inventory_items_tenant_id"),
        UniqueConstraint("tenant_id", "sku", name="uq_inventory_items_tenant_sku"),
        CheckConstraint("conversion_factor > 0", name="ck_inventory_items_conversion"),
        Index("ix_inventory_items_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    base_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    whole_unit: Mapped[str | None] = mapped_column(String(64))
    fraction_unit: Mapped[str | None] = mapped_column(String(64))
    conversion_factor: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False, default=Decimal("1"))
    category: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=inventory_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow, onupdate=inventory_utcnow
    )


class InventoryItemAliasModel(Base):
    __tablename__ = "inventory_item_aliases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"], ["inventory_items.tenant_id", "inventory_items.id"],
            ondelete="CASCADE", name="fk_inventory_aliases_tenant_item",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_aliases_tenant_id"),
        UniqueConstraint("tenant_id", "normalized_alias", name="uq_inventory_aliases_tenant_normalized"),
        Index("ix_inventory_aliases_tenant_item", "tenant_id", "item_id"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, nullable=False)
    item_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=inventory_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow, onupdate=inventory_utcnow
    )


class InventoryDocumentModel(Base):
    __tablename__ = "inventory_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "location_id"],
            ["inventory_locations.tenant_id", "inventory_locations.id"],
            ondelete="RESTRICT",
            name="fk_inventory_documents_tenant_location",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_documents_tenant_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_inventory_documents_tenant_key"),
        CheckConstraint(
            "document_type IN ('stock_count','warehouse_transfer','waste','unclassified')",
            name="ck_inventory_documents_type",
        ),
        CheckConstraint(
            "status IN ('collecting','preparing','prepared','duplicate','retryable_failure','terminal_failure','analyzing','needs_review','approved','rejected','finalized')",
            name="ck_inventory_documents_status",
        ),
        CheckConstraint("expected_pages >= 0", name="ck_inventory_documents_expected_pages"),
        CheckConstraint("received_pages >= 0", name="ck_inventory_documents_received_pages"),
        Index("ix_inventory_documents_tenant_date", "tenant_id", "business_date", "status"),
        Index("ix_inventory_documents_tenant_location", "tenant_id", "location_id", "business_date"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    location_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="collecting")
    expected_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_by: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )


class InventoryDocumentPageModel(Base):
    __tablename__ = "inventory_document_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["inventory_documents.tenant_id", "inventory_documents.id"],
            ondelete="CASCADE",
            name="fk_inventory_pages_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_file_id"],
            ["inventory_source_files.tenant_id", "inventory_source_files.id"],
            ondelete="RESTRICT",
            name="fk_inventory_pages_tenant_source_file",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "duplicate_of_page_id"],
            ["inventory_document_pages.tenant_id", "inventory_document_pages.id"],
            ondelete="SET NULL",
            name="fk_inventory_pages_tenant_duplicate",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_pages_tenant_id"),
        UniqueConstraint("tenant_id", "source_file_id", name="uq_inventory_pages_tenant_source_file"),
        UniqueConstraint(
            "tenant_id", "document_id", "page_number",
            name="uq_inventory_pages_document_number",
        ),
        CheckConstraint("page_number > 0", name="ck_inventory_pages_number"),
        CheckConstraint("page_count > 0", name="ck_inventory_pages_count"),
        CheckConstraint(
            "analysis_status IN ('pending','processing','completed','failed','skipped')",
            name="ck_inventory_pages_analysis_status",
        ),
        CheckConstraint(
            "preparation_status IN ('queued','preparing','prepared','duplicate','retryable_failure','terminal_failure')",
            name="ck_inventory_pages_preparation_status",
        ),
        Index("ix_inventory_pages_content_hash", "tenant_id", "content_sha256"),
        Index("ix_inventory_pages_document", "tenant_id", "document_id", "page_number"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, nullable=False)
    document_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    source_file_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    drive_file_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    duplicate_of_page_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    preparation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    preparation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued"
    )
    prepared_storage_key: Mapped[str | None] = mapped_column(String(1024))
    prepared_content_sha256: Mapped[str | None] = mapped_column(String(64))
    prepared_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    prepared_mime_type: Mapped[str | None] = mapped_column(String(255))
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    preparation_error_code: Mapped[str | None] = mapped_column(String(100))
    preparation_error_message: Mapped[str | None] = mapped_column(Text)
    analysis_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )


class InventoryAiAnalysisModel(Base):
    __tablename__ = "inventory_ai_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["inventory_documents.tenant_id", "inventory_documents.id"],
            ondelete="CASCADE",
            name="fk_inventory_ai_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "page_id"],
            ["inventory_document_pages.tenant_id", "inventory_document_pages.id"],
            ondelete="CASCADE",
            name="fk_inventory_ai_tenant_page",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_ai_tenant_id"),
        UniqueConstraint(
            "tenant_id", "page_id", "analysis_version",
            name="uq_inventory_ai_page_version",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_inventory_ai_tenant_key"),
        CheckConstraint("analysis_version > 0", name="ck_inventory_ai_version"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_inventory_ai_confidence",
        ),
        CheckConstraint(
            "status IN ('pending','processing','completed','failed','queued','analyzing','succeeded','retryable_failure','terminal_failure','superseded')",
            name="ck_inventory_ai_status",
        ),
        CheckConstraint(
            "validation_status IN ('unvalidated','valid','invalid','needs_review')",
            name="ck_inventory_ai_validation",
        ),
        Index("ix_inventory_ai_document", "tenant_id", "document_id", "created_at"),
        Index("ix_inventory_ai_page_status", "tenant_id", "page_id", "status"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, nullable=False)
    document_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    page_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    extraction_profile: Mapped[str] = mapped_column(String(128), nullable=False, default="inventory-stock-sheet")
    extraction_profile_version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    usage_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    estimated_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    validation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unvalidated"
    )
    confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)
    raw_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Phase 5 keeps extraction separate from future normalization.
    normalized_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    extracted_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )



class InventoryLineModel(Base):
    __tablename__ = "inventory_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["inventory_documents.tenant_id", "inventory_documents.id"],
            ondelete="CASCADE",
            name="fk_inventory_lines_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "page_id"],
            ["inventory_document_pages.tenant_id", "inventory_document_pages.id"],
            ondelete="CASCADE",
            name="fk_inventory_lines_tenant_page",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "analysis_id"],
            ["inventory_ai_analyses.tenant_id", "inventory_ai_analyses.id"],
            ondelete="RESTRICT",
            name="fk_inventory_lines_tenant_analysis",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            ["inventory_items.tenant_id", "inventory_items.id"],
            ondelete="RESTRICT",
            name="fk_inventory_lines_tenant_item",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_lines_tenant_id"),
        UniqueConstraint(
            "tenant_id", "analysis_id", "line_number",
            name="uq_inventory_lines_analysis_number",
        ),
        CheckConstraint("line_number > 0", name="ck_inventory_lines_number"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_inventory_lines_confidence",
        ),
        CheckConstraint(
            "validation_status IN ('unvalidated','valid','invalid','needs_review','corrected')",
            name="ck_inventory_lines_validation",
        ),
        Index("ix_inventory_lines_document", "tenant_id", "document_id", "line_number"),
        Index("ix_inventory_lines_item", "tenant_id", "item_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, nullable=False)
    document_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    page_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    analysis_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_item_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    item_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    raw_values_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    normalized_values_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    whole_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    fraction_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    whole_unit: Mapped[str | None] = mapped_column(String(64))
    fraction_unit: Mapped[str | None] = mapped_column(String(64))
    conversion_factor_snapshot: Mapped[Decimal | None] = mapped_column(QUANTITY)
    quantity_base_unit: Mapped[Decimal | None] = mapped_column(QUANTITY)
    waste_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    waste_reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)
    validation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unvalidated"
    )
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )


class InventoryReviewModel(Base):
    __tablename__ = "inventory_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["inventory_documents.tenant_id", "inventory_documents.id"],
            ondelete="CASCADE",
            name="fk_inventory_reviews_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "line_id"],
            ["inventory_lines.tenant_id", "inventory_lines.id"],
            ondelete="CASCADE",
            name="fk_inventory_reviews_tenant_line",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_reviews_tenant_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_inventory_reviews_tenant_key"),
        CheckConstraint(
            "status IN ('pending','in_review','approved','rejected','cancelled')",
            name="ck_inventory_reviews_status",
        ),
        Index("ix_inventory_reviews_queue", "tenant_id", "status", "created_at"),
        Index("ix_inventory_reviews_document", "tenant_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    line_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    original_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    suggested_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    final_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reviewer_id: Mapped[str | None] = mapped_column(String(255))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )


class InventoryTransactionModel(Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "location_id"],
            ["inventory_locations.tenant_id", "inventory_locations.id"],
            ondelete="RESTRICT",
            name="fk_inventory_transactions_tenant_location",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            ["inventory_items.tenant_id", "inventory_items.id"],
            ondelete="RESTRICT",
            name="fk_inventory_transactions_tenant_item",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_document_id"],
            ["inventory_documents.tenant_id", "inventory_documents.id"],
            ondelete="RESTRICT",
            name="fk_inventory_transactions_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_line_id"],
            ["inventory_lines.tenant_id", "inventory_lines.id"],
            ondelete="RESTRICT",
            name="fk_inventory_transactions_tenant_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reverses_transaction_id"],
            ["inventory_transactions.tenant_id", "inventory_transactions.id"],
            ondelete="RESTRICT",
            name="fk_inventory_transactions_tenant_reversal",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_transactions_tenant_id"),
        UniqueConstraint(
            "tenant_id", "idempotency_key",
            name="uq_inventory_transactions_tenant_key",
        ),
        CheckConstraint(
            "transaction_type IN ('opening_balance','receipt','transfer_out','transfer_in','closing_count','waste','usage_adjustment')",
            name="ck_inventory_transactions_type",
        ),
        CheckConstraint(
            "status IN ('posted','reversed')",
            name="ck_inventory_transactions_status",
        ),
        CheckConstraint("conversion_factor_snapshot > 0", name="ck_inventory_transactions_conversion"),
        Index(
            "ix_inventory_transactions_ledger",
            "tenant_id", "business_date", "location_id", "item_id", "created_at",
        ),
        Index("ix_inventory_transactions_document", "tenant_id", "source_document_id"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date)
    location_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    item_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_base_unit: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    base_unit_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    conversion_factor_snapshot: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    source_document_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    source_line_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="posted")
    reverses_transaction_id: Mapped[str | None] = mapped_column(ENTITY_ID)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )


class InventoryDailyRunModel(Base):
    __tablename__ = "inventory_daily_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inventory_daily_runs_tenant_id"),
        UniqueConstraint(
            "tenant_id", "business_date",
            name="uq_inventory_daily_runs_tenant_date",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key",
            name="uq_inventory_daily_runs_tenant_key",
        ),
        CheckConstraint(
            "status IN ('open','checking','ready','finalized','failed')",
            name="ck_inventory_daily_runs_status",
        ),
        CheckConstraint("report_version >= 0", name="ck_inventory_daily_runs_version"),
        Index("ix_inventory_daily_runs_status", "tenant_id", "status", "business_date"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(
        TENANT_ID, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    business_date: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    location_state_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    finalized_with_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finalized_by: Mapped[str | None] = mapped_column(String(255))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )


class InventoryExportModel(Base):
    __tablename__ = "inventory_exports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "daily_run_id"],
            ["inventory_daily_runs.tenant_id", "inventory_daily_runs.id"],
            ondelete="CASCADE",
            name="fk_inventory_exports_tenant_daily_run",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_exports_tenant_id"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_inventory_exports_tenant_key"),
        UniqueConstraint(
            "tenant_id", "daily_run_id", "export_version", "export_format",
            name="uq_inventory_exports_run_version",
        ),
        CheckConstraint("export_version > 0", name="ck_inventory_exports_version"),
        CheckConstraint(
            "status IN ('pending','generating','completed','failed')",
            name="ck_inventory_exports_status",
        ),
        CheckConstraint(
            "export_format IN ('xlsx','xlsm','csv')",
            name="ck_inventory_exports_format",
        ),
        Index("ix_inventory_exports_run", "tenant_id", "daily_run_id", "created_at"),
        Index("ix_inventory_exports_status", "tenant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(ENTITY_ID, primary_key=True, default=new_inventory_id)
    tenant_id: Mapped[str] = mapped_column(TENANT_ID, nullable=False)
    daily_run_id: Mapped[str] = mapped_column(ENTITY_ID, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    export_version: Mapped[int] = mapped_column(Integer, nullable=False)
    export_format: Mapped[str] = mapped_column(String(16), nullable=False, default="xlsx")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date)
    drive_file_id: Mapped[str | None] = mapped_column(String(2048))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=inventory_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=inventory_utcnow, onupdate=inventory_utcnow,
    )

