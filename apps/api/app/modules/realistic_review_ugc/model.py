from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKeyConstraint,
    JSON,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RrugcCampaignModel(Base):
    __tablename__ = "rrugc_campaigns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_campaign_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_campaign_product",
            ondelete="RESTRICT",
        ),
        Index("ix_rrugc_campaign_tenant_status", "tenant_id", "status", "updated_at"),
        Index("ix_rrugc_campaign_product", "tenant_id", "product_id", "updated_at"),
        Index(
            "ix_rrugc_campaign_autoscout_due",
            "tenant_id",
            "status",
            "auto_scout",
            "scan_next_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    search_queries_json: Mapped[list | None] = mapped_column(JSON)
    search_query_anchors_json: Mapped[list | None] = mapped_column(JSON)
    discovery_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="keyword")
    product_context_json: Mapped[dict | None] = mapped_column(JSON)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_scroll_batches: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    auto_import: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_scout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    scan_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_lease_agent_id: Mapped[str | None] = mapped_column(String(36))
    scan_lease_run_id: Mapped[str | None] = mapped_column(String(36))
    scan_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scan_empty_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scan_failure_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scan_last_error_code: Mapped[str | None] = mapped_column(String(100))
    min_head_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.18)
    max_head_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.70)
    min_smile_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.00)
    max_head_occlusion: Mapped[float] = mapped_column(Float, nullable=False, default=0.65)
    max_ai_risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    min_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.60)
    min_ugc_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.55)
    min_product_fit_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.55)
    require_head_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reject_headwear: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    product_id: Mapped[str | None] = mapped_column(String(36))
    product_revision: Mapped[int | None] = mapped_column(Integer)
    product_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    product_reference_snapshot_json: Mapped[list | None] = mapped_column(JSON)
    product_variant_ids_json: Mapped[list | None] = mapped_column(JSON)
    product_bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    auto_complete_on_delivery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completion_destination_id: Mapped[str | None] = mapped_column(String(36))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scout_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scout_status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    scout_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RrugcSourcePlanModel(Base):
    __tablename__ = "rrugc_source_plans"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "root_folder_id",
            "source_file_id",
            name="uq_rrugc_source_plan_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_source_plan_campaign",
            ondelete="SET NULL",
        ),
        Index(
            "ix_rrugc_source_plan_tenant_status",
            "tenant_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_rrugc_source_plan_campaign",
            "tenant_id",
            "campaign_id",
        ),
        Index(
            "ix_rrugc_source_plan_embroidery_signature",
            "tenant_id",
            "embroidery_signature",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    root_folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_parent_folder_id: Mapped[str | None] = mapped_column(String(255))
    source_relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    source_mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_size_bytes: Mapped[int | None] = mapped_column(Integer)
    source_width: Mapped[int | None] = mapped_column(Integer)
    source_height: Mapped[int | None] = mapped_column(Integer)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_web_url: Mapped[str | None] = mapped_column(Text)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    embroidery_signature: Mapped[str | None] = mapped_column(String(64))
    target_count: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    visual_context_json: Mapped[dict | None] = mapped_column(JSON)
    campaign_id: Mapped[str | None] = mapped_column(String(36))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcKeywordVolumeModel(Base):
    __tablename__ = "rrugc_keyword_volumes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "keyword_normalized",
            name="uq_rrugc_keyword_volume_keyword",
        ),
        Index(
            "ix_rrugc_keyword_volume_search",
            "tenant_id",
            "search_volume",
        ),
        Index(
            "ix_rrugc_keyword_volume_fetched",
            "tenant_id",
            "fetched_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    keyword: Mapped[str] = mapped_column(String(500), nullable=False)
    keyword_normalized: Mapped[str] = mapped_column(String(500), nullable=False)
    search_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    competition: Mapped[str | None] = mapped_column(String(32))
    cpc_low: Mapped[float | None] = mapped_column(Float)
    cpc_high: Mapped[float | None] = mapped_column(Float)
    source_image_url: Mapped[str | None] = mapped_column(String(2048))
    source_pin_url: Mapped[str | None] = mapped_column(String(2048))
    picked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    picked_by_user_id: Mapped[str | None] = mapped_column(String(255))
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    favorite_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    favorite_by_user_id: Mapped[str | None] = mapped_column(String(255))
    # Trademark is independent of Google Ads volume; never infer 'safe' from CPC.
    trademark_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified")
    trademark_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trademark_source: Mapped[str | None] = mapped_column(String(64))
    trademark_match_count: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, default="aebrowse_google_ads"
    )
    provider_account: Mapped[str | None] = mapped_column(String(255))
    provider_customer_id: Mapped[str | None] = mapped_column(String(64))
    provider_raw_json: Mapped[dict | None] = mapped_column(JSON)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcScoutFeedbackModel(Base):
    __tablename__ = "rrugc_scout_feedback"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target_type", "target_key", name="uq_rrugc_scout_feedback_target"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_key: Mapped[str] = mapped_column(String(500), nullable=False)
    display_value: Mapped[str] = mapped_column(String(2048), nullable=False)
    keyword: Mapped[str] = mapped_column(String(500), nullable=False)
    source_image_url: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    updated_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    claimed_by_agent_id: Mapped[str | None] = mapped_column(String(36))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))



class RrugcScoutQueryModel(Base):
    """Tenant-scoped query pool with cross-machine exclusive search leases."""
    __tablename__ = "rrugc_scout_queries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "query_normalized", name="uq_rrugc_scout_query_unique"),
        Index("ix_rrugc_scout_query_lease", "tenant_id", "lease_expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(String(240), nullable=False)
    query_normalized: Mapped[str] = mapped_column(String(240), nullable=False)
    lane: Mapped[str] = mapped_column(String(20), nullable=False)
    source_keyword: Mapped[str | None] = mapped_column(String(500))
    completed_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned_pins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    found_quotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_keywords: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_pins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    empty_cycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_by_agent_id: Mapped[str | None] = mapped_column(String(36))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class RrugcScoutMetricCycleModel(Base):
    __tablename__ = "rrugc_scout_metric_cycles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "mode", "cycle_id", name="uq_rrugc_scout_metric_cycle"),
        Index("ix_rrugc_scout_metric_cycle_tenant", "tenant_id", "mode", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    machine_label: Mapped[str] = mapped_column(String(160), nullable=False, default="unknown")
    cycle_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scanned_pins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    found_quotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_keywords: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_pins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RrugcScoutAgentModel(Base):
    __tablename__ = "rrugc_scout_agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_scout_agent_tenant_id"),
        Index(
            "ix_rrugc_scout_agent_tenant_active",
            "tenant_id",
            "active",
            "last_seen_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    client_version: Mapped[str | None] = mapped_column(String(64))
    machine_label: Mapped[str | None] = mapped_column(String(160))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcScoutRunModel(Base):
    __tablename__ = "rrugc_scout_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_scout_run_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "agent_id"],
            ["rrugc_scout_agents.tenant_id", "rrugc_scout_agents.id"],
            name="fk_rrugc_scout_run_agent",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_rrugc_scout_run_campaign_created",
            "tenant_id",
            "campaign_id",
            "created_at",
        ),
        Index(
            "ix_rrugc_scout_run_agent_status",
            "tenant_id",
            "agent_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="claimed")
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_scroll_batches: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_import: Mapped[bool] = mapped_column(Boolean, nullable=False)
    progress_before: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    existing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    keyword_stats_json: Mapped[dict | None] = mapped_column(JSON)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcProductModel(Base):
    __tablename__ = "rrugc_products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_rrugc_product_tenant_sku"),
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_product_tenant_id"),
        Index("ix_rrugc_product_tenant_status", "tenant_id", "status", "updated_at"),
        Index(
            "ix_rrugc_product_tenant_source_url",
            "tenant_id",
            "source_url",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    product_type: Mapped[str] = mapped_column(String(64), nullable=False, default="hat")
    color: Mapped[str | None] = mapped_column(String(120))
    material: Mapped[str | None] = mapped_column(String(200))
    crown_profile: Mapped[str | None] = mapped_column(String(64))
    crown_height_mm: Mapped[float | None] = mapped_column(Float)
    brim_style: Mapped[str | None] = mapped_column(String(64))
    brim_length_mm: Mapped[float | None] = mapped_column(Float)
    circumference_mm: Mapped[float | None] = mapped_column(Float)
    logo_position: Mapped[str | None] = mapped_column(String(120))
    fit_notes: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_host: Mapped[str | None] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(200))
    source_description: Mapped[str | None] = mapped_column(Text)
    source_category: Mapped[str | None] = mapped_column(String(200))
    source_price_text: Mapped[str | None] = mapped_column(String(120))
    source_currency: Mapped[str | None] = mapped_column(String(16))
    source_images_json: Mapped[list | None] = mapped_column(JSON)
    source_variants_json: Mapped[list | None] = mapped_column(JSON)
    source_metadata_json: Mapped[dict | None] = mapped_column(JSON)
    source_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcProductVariantModel(Base):
    __tablename__ = "rrugc_product_variants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_product_variant_product",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "product_id", "source_variant_id",
            name="uq_rrugc_product_variant_source",
        ),
        UniqueConstraint(
            "tenant_id", "product_id", "id",
            name="uq_rrugc_product_variant_tenant_product_id",
        ),
        Index(
            "ix_rrugc_product_variant_product",
            "tenant_id", "product_id", "status", "position",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_variant_id: Mapped[str] = mapped_column(String(120), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(300))
    color: Mapped[str | None] = mapped_column(String(120))
    size: Mapped[str | None] = mapped_column(String(120))
    price_text: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str | None] = mapped_column(String(16))
    image_urls_json: Mapped[list | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcProductReferenceModel(Base):
    __tablename__ = "rrugc_product_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_product_reference_product",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "product_id", "view_type", "version",
            name="uq_rrugc_product_reference_version",
        ),
        Index(
            "ix_rrugc_product_reference_product_view",
            "tenant_id", "product_id", "view_type", "version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id", "variant_id"],
            [
                "rrugc_product_variants.tenant_id",
                "rrugc_product_variants.product_id",
                "rrugc_product_variants.id",
            ],
            name="fk_rrugc_product_reference_variant",
            ondelete="CASCADE",
        ),
        Index(
            "ix_rrugc_product_reference_hash",
            "tenant_id", "content_hash",
        ),
        Index(
            "ix_rrugc_product_reference_variant",
            "tenant_id", "product_id", "variant_id", "view_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    variant_id: Mapped[str | None] = mapped_column(String(36))
    view_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    image_format: Mapped[str] = mapped_column(String(16), nullable=False)
    remote_file_id: Mapped[str | None] = mapped_column(String(255))
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    reused_storage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcCandidateModel(Base):
    __tablename__ = "rrugc_candidates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_candidate_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_candidate_campaign",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "campaign_id", "source_key",
            name="uq_rrugc_candidate_source",
        ),
        Index(
            "ix_rrugc_candidate_campaign_status",
            "tenant_id", "campaign_id", "status", "created_at",
        ),
        Index(
            "ix_rrugc_candidate_content_hash",
            "tenant_id", "content_hash",
        ),
        Index(
            "ix_rrugc_candidate_tenant_source_key",
            "tenant_id", "source_key",
        ),
        Index(
            "ix_rrugc_candidate_campaign_created",
            "tenant_id", "campaign_id", "created_at",
        ),
        Index(
            "ix_rrugc_candidate_campaign_diversity",
            "tenant_id", "campaign_id", "diversity_signature", "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    pin_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="analysis_queued")
    analysis_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    import_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    people_count: Mapped[int | None] = mapped_column(Integer)
    primary_head_ratio: Mapped[float | None] = mapped_column(Float)
    smile_score: Mapped[float | None] = mapped_column(Float)
    head_visible: Mapped[bool | None] = mapped_column(Boolean)
    existing_headwear: Mapped[bool | None] = mapped_column(Boolean)
    head_occlusion: Mapped[float | None] = mapped_column(Float)
    mobile_ugc_score: Mapped[float | None] = mapped_column(Float)
    phone_authenticity_score: Mapped[float | None] = mapped_column(Float)
    artistic_editorial_risk: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    ai_risk_score: Mapped[float | None] = mapped_column(Float)
    ai_risk_raw_score: Mapped[float | None] = mapped_column(Float)
    ai_detector_confidence: Mapped[float | None] = mapped_column(Float)
    ai_risk_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    ai_signal_json: Mapped[dict | None] = mapped_column(JSON)
    diversity_signature: Mapped[str | None] = mapped_column(String(255))
    ai_manual_label: Mapped[str | None] = mapped_column(String(16))
    ai_manual_note: Mapped[str | None] = mapped_column(Text)
    ai_manual_reviewed_by_user_id: Mapped[str | None] = mapped_column(String(255))
    ai_manual_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    product_fit_score: Mapped[float | None] = mapped_column(Float)
    matched_variant_id: Mapped[str | None] = mapped_column(String(36))
    matched_variant_name: Mapped[str | None] = mapped_column(String(300))
    matched_color: Mapped[str | None] = mapped_column(String(120))
    color_match_score: Mapped[float | None] = mapped_column(Float)
    product_shape_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float | None] = mapped_column(Float)
    reject_reason: Mapped[str | None] = mapped_column(String(64))
    analyzer_provider: Mapped[str | None] = mapped_column(String(64))
    analyzer_model: Mapped[str | None] = mapped_column(String(128))
    analyzer_version: Mapped[str | None] = mapped_column(String(64))
    analysis_summary: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    image_format: Mapped[str | None] = mapped_column(String(16))
    remote_file_id: Mapped[str | None] = mapped_column(String(255))
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcReferenceAssetModel(Base):
    __tablename__ = "rrugc_reference_assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rrugc_reference_asset_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_key",
            name="uq_rrugc_reference_asset_source",
        ),
        UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_rrugc_reference_asset_content_hash",
        ),
        Index(
            "ix_rrugc_reference_asset_library",
            "tenant_id",
            "status",
            "source_type",
            "updated_at",
        ),
        Index(
            "ix_rrugc_reference_asset_campaign",
            "tenant_id",
            "source_campaign_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(500))
    source_campaign_id: Mapped[str | None] = mapped_column(String(36))
    source_candidate_id: Mapped[str | None] = mapped_column(String(36))
    profile_key: Mapped[str | None] = mapped_column(String(100))
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    image_format: Mapped[str | None] = mapped_column(String(16))
    tags_json: Mapped[list | None] = mapped_column(JSON)
    themes_json: Mapped[list | None] = mapped_column(JSON)
    quality_score: Mapped[float | None] = mapped_column(Float)
    visual_score: Mapped[float | None] = mapped_column(Float)
    context_score: Mapped[float | None] = mapped_column(Float)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remote_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcReferenceSetModel(Base):
    __tablename__ = "rrugc_reference_sets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_rrugc_reference_set_tenant_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_reference_set_campaign",
            ondelete="CASCADE",
        ),
        Index(
            "ix_rrugc_reference_set_library",
            "tenant_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_rrugc_reference_set_campaign",
            "tenant_id",
            "campaign_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(36))
    profile_key: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcReferenceSetItemModel(Base):
    __tablename__ = "rrugc_reference_set_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "reference_set_id"],
            ["rrugc_reference_sets.tenant_id", "rrugc_reference_sets.id"],
            name="fk_rrugc_reference_set_item_set",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reference_asset_id"],
            ["rrugc_reference_assets.tenant_id", "rrugc_reference_assets.id"],
            name="fk_rrugc_reference_set_item_asset",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "reference_set_id",
            "role",
            "reference_asset_id",
            name="uq_rrugc_reference_set_item_binding",
        ),
        Index(
            "ix_rrugc_reference_set_item_order",
            "tenant_id",
            "reference_set_id",
            "position",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_set_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reference_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcReferenceSeedModel(Base):
    __tablename__ = "rrugc_reference_seeds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_reference_seed_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reference_asset_id"],
            ["rrugc_reference_assets.tenant_id", "rrugc_reference_assets.id"],
            name="fk_rrugc_reference_seed_asset",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "profile_key",
            "reference_asset_id",
            name="uq_rrugc_reference_seed_scope",
        ),
        Index(
            "ix_rrugc_reference_seed_scope",
            "tenant_id",
            "campaign_id",
            "profile_key",
            "label",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reference_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    profile_key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcVisualFingerprintModel(Base):
    __tablename__ = "rrugc_visual_fingerprints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["rrugc_candidates.tenant_id", "rrugc_candidates.id"],
            name="fk_rrugc_visual_fingerprint_candidate",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "candidate_id", "fingerprint",
            name="uq_rrugc_visual_fingerprint_candidate",
        ),
        Index(
            "ix_rrugc_visual_fingerprint_tenant",
            "tenant_id", "fingerprint",
        ),
        Index(
            "ix_rrugc_visual_fingerprint_campaign",
            "tenant_id", "campaign_id", "candidate_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RrugcAiFeedbackModel(Base):
    __tablename__ = "rrugc_ai_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["rrugc_candidates.tenant_id", "rrugc_candidates.id"],
            name="fk_rrugc_ai_feedback_candidate",
            ondelete="CASCADE",
        ),
        Index(
            "ix_rrugc_ai_feedback_tenant_label_created",
            "tenant_id", "label", "created_at",
        ),
        Index(
            "ix_rrugc_ai_feedback_candidate_created",
            "tenant_id", "candidate_id", "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    ai_risk_raw_score: Mapped[float | None] = mapped_column(Float)
    ai_risk_score: Mapped[float | None] = mapped_column(Float)
    detector_confidence: Mapped[float | None] = mapped_column(Float)
    analyzer_version: Mapped[str | None] = mapped_column(String(64))
    signal_json: Mapped[dict | None] = mapped_column(JSON)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)





class RrugcStage2SkillRegistryModel(Base):
    __tablename__ = "rrugc_stage2_skill_registry"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source",
            "skill_name",
            name="uq_rrugc_stage2_skill_registry_name",
        ),
        Index(
            "ix_rrugc_stage2_skill_registry_tenant_enabled",
            "tenant_id",
            "enabled",
            "updated_at",
        ),
        Index(
            "ix_rrugc_stage2_skill_registry_skill_id",
            "tenant_id",
            "skill_id",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    skill_id: Mapped[str | None] = mapped_column(String(255))
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workflow: Mapped[str] = mapped_column(String(64), nullable=False, default="image_studio")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_version: Mapped[str | None] = mapped_column(String(64))
    latest_version: Mapped[str | None] = mapped_column(String(64))
    synced_version: Mapped[str | None] = mapped_column(String(64))
    sync_state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_synced")
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="valid")
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str | None] = mapped_column(String(255))
    updated_by_user_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class RrugcStage2SkillVersionModel(Base):
    __tablename__ = "rrugc_stage2_skill_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["registry_id"],
            ["rrugc_stage2_skill_registry.id"],
            name="fk_rrugc_stage2_skill_version_registry",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "registry_id",
            "version",
            name="uq_rrugc_stage2_skill_version",
        ),
        Index(
            "ix_rrugc_stage2_skill_version_registry",
            "tenant_id",
            "registry_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    registry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class RrugcKeywordImageJobModel(Base):
    """One durable Skill generation for each Stage 0 used keyword.

    A failed run can be retried, but a completed output is never overwritten.
    """
    __tablename__ = "rrugc_keyword_image_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["keyword_id"], ["rrugc_keyword_volumes.id"],
                             name="fk_rrugc_keyword_image_job_keyword", ondelete="RESTRICT"),
        UniqueConstraint("tenant_id", "keyword_id", name="uq_rrugc_keyword_image_job_keyword"),
        Index("ix_rrugc_keyword_image_job_status", "tenant_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    keyword_id: Mapped[str] = mapped_column(String(36), nullable=False)
    keyword_text: Mapped[str] = mapped_column(String(500), nullable=False)
    skill_source: Mapped[str] = mapped_column(String(32), nullable=False)
    skill_id: Mapped[str | None] = mapped_column(String(255))
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_version: Mapped[str | None] = mapped_column(String(64))
    skill_bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    output_remote_file_id: Mapped[str | None] = mapped_column(String(255))
    output_content_type: Mapped[str | None] = mapped_column(String(128))
    output_size_bytes: Mapped[int | None] = mapped_column(Integer)
    output_width: Mapped[int | None] = mapped_column(Integer)
    output_height: Mapped[int | None] = mapped_column(Integer)
    output_web_url: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcStage2JobModel(Base):
    __tablename__ = "rrugc_stage2_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_plan_id"],
            ["rrugc_source_plans.id"],
            name="fk_rrugc_stage2_job_source_plan",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_stage2_job_campaign",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_stage2_job_idempotency",
        ),
        Index(
            "ix_rrugc_stage2_job_source_created",
            "tenant_id",
            "source_plan_id",
            "created_at",
        ),
        Index(
            "ix_rrugc_stage2_job_status_created",
            "tenant_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_source: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    skill_id: Mapped[str | None] = mapped_column(String(255))
    skill_version: Mapped[str | None] = mapped_column(String(64))
    skill_bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    selected_candidate_ids_json: Mapped[list] = mapped_column(JSON, nullable=False)
    selected_reference_snapshot_json: Mapped[list] = mapped_column(JSON, nullable=False)
    selected_source_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    prompt_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    output_content_hash: Mapped[str | None] = mapped_column(String(64))
    output_content_type: Mapped[str | None] = mapped_column(String(128))
    output_size_bytes: Mapped[int | None] = mapped_column(Integer)
    output_width: Mapped[int | None] = mapped_column(Integer)
    output_height: Mapped[int | None] = mapped_column(Integer)
    output_remote_file_id: Mapped[str | None] = mapped_column(String(255))
    output_remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    output_web_url: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcStage3AnalysisModel(Base):
    __tablename__ = "rrugc_stage3_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage2_job_id"],
            ["rrugc_stage2_jobs.id"],
            name="fk_rrugc_stage3_analysis_stage2_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "stage2_job_id",
            name="uq_rrugc_stage3_analysis_stage2_job",
        ),
        Index(
            "ix_rrugc_stage3_analysis_status_updated",
            "tenant_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage2_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    output_content_hash: Mapped[str | None] = mapped_column(String(64))
    analysis_version: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    people_count: Mapped[int | None] = mapped_column(Integer)
    person_visible: Mapped[bool | None] = mapped_column(Boolean)
    hat_visible: Mapped[bool | None] = mapped_column(Boolean)
    product_visible: Mapped[bool | None] = mapped_column(Boolean)
    embroidery_visible: Mapped[bool | None] = mapped_column(Boolean)
    mobile_ugc_score: Mapped[float | None] = mapped_column(Float)
    photorealism_score: Mapped[float | None] = mapped_column(Float)
    product_visibility_score: Mapped[float | None] = mapped_column(Float)
    review_fit_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float | None] = mapped_column(Float)
    scene_type: Mapped[str | None] = mapped_column(String(64))
    framing_type: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    reviewer_name: Mapped[str | None] = mapped_column(String(80))
    star_rating: Mapped[int | None] = mapped_column(Integer)
    review_text: Mapped[str | None] = mapped_column(Text)
    review_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_json: Mapped[list | None] = mapped_column(JSON)
    reject_reasons_json: Mapped[list | None] = mapped_column(JSON)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcGenerationAttemptModel(Base):
    __tablename__ = "rrugc_generation_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_generation_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_id"],
            ["rrugc_candidates.tenant_id", "rrugc_candidates.id"],
            name="fk_rrugc_generation_candidate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["rrugc_products.tenant_id", "rrugc_products.id"],
            name="fk_rrugc_generation_product",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key",
            name="uq_rrugc_generation_attempt_idempotency",
        ),
        Index(
            "ix_rrugc_generation_campaign_status",
            "tenant_id", "campaign_id", "status", "created_at",
        ),
        Index(
            "ix_rrugc_generation_candidate",
            "tenant_id", "candidate_id", "created_at",
        ),
        Index(
            "ix_rrugc_generation_output_hash",
            "tenant_id", "output_content_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    product_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    product_reference_snapshot_json: Mapped[list] = mapped_column(JSON, nullable=False)
    candidate_snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    generation_variant: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    worker_skill_version: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_model: Mapped[str | None] = mapped_column(String(128))
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    prompt_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="prepared")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    output_content_hash: Mapped[str | None] = mapped_column(String(64))
    output_content_type: Mapped[str | None] = mapped_column(String(128))
    output_size_bytes: Mapped[int | None] = mapped_column(Integer)
    output_width: Mapped[int | None] = mapped_column(Integer)
    output_height: Mapped[int | None] = mapped_column(Integer)
    output_remote_file_id: Mapped[str | None] = mapped_column(String(255))
    output_remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    output_web_url: Mapped[str | None] = mapped_column(Text)
    parent_attempt_id: Mapped[str | None] = mapped_column(String(36))
    correction_supervisor_result_id: Mapped[str | None] = mapped_column(String(36))
    supervisor_correction_json: Mapped[dict | None] = mapped_column(JSON)
    review_status: Mapped[str | None] = mapped_column(String(32))
    review_task_id: Mapped[str | None] = mapped_column(String(36))
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    export_status: Mapped[str | None] = mapped_column(String(32))
    export_record_id: Mapped[str | None] = mapped_column(String(36))
    catalog_asset_id: Mapped[str | None] = mapped_column(String(36))
    exported_by_user_id: Mapped[str | None] = mapped_column(String(255))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcSupervisorResultModel(Base):
    __tablename__ = "rrugc_supervisor_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_attempt_id"],
            ["rrugc_generation_attempts.id"],
            name="fk_rrugc_supervisor_generation_attempt",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "generation_attempt_id",
            "supervisor_skill_version",
            name="uq_rrugc_supervisor_attempt_skill",
        ),
        Index(
            "ix_rrugc_supervisor_campaign_status",
            "tenant_id",
            "campaign_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_rrugc_supervisor_attempt",
            "tenant_id",
            "generation_attempt_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    supervisor_skill_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    reason: Mapped[str | None] = mapped_column(String(100))
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    expected_json: Mapped[dict | None] = mapped_column(JSON)
    correction_json: Mapped[dict | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_model: Mapped[str | None] = mapped_column(String(128))
    processing_job_id: Mapped[str | None] = mapped_column(String(36))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcReviewTaskModel(Base):
    __tablename__ = "rrugc_review_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_attempt_id"],
            ["rrugc_generation_attempts.id"],
            name="fk_rrugc_review_task_generation_attempt",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["supervisor_result_id"],
            ["rrugc_supervisor_results.id"],
            name="fk_rrugc_review_task_supervisor_result",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "generation_attempt_id",
            name="uq_rrugc_review_task_attempt",
        ),
        Index(
            "ix_rrugc_review_task_status_priority",
            "tenant_id",
            "status",
            "priority",
            "created_at",
        ),
        Index(
            "ix_rrugc_review_task_campaign",
            "tenant_id",
            "campaign_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    supervisor_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    queue_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="standard")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcExportModel(Base):
    __tablename__ = "rrugc_exports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["generation_attempt_id"],
            ["rrugc_generation_attempts.id"],
            name="fk_rrugc_export_generation_attempt",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["review_task_id"],
            ["rrugc_review_tasks.id"],
            name="fk_rrugc_export_review_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_rrugc_export_catalog_asset",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "generation_attempt_id",
            name="uq_rrugc_export_attempt",
        ),
        Index(
            "ix_rrugc_export_campaign_status",
            "tenant_id",
            "campaign_id",
            "status",
            "exported_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    generation_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    review_task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    catalog_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    storage_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    remote_folder_id: Mapped[str | None] = mapped_column(String(255))
    web_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="exported")
    requested_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

class RrugcDeliveryDestinationModel(Base):
    __tablename__ = "rrugc_delivery_destinations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_rrugc_delivery_destination_name",
        ),
        Index(
            "ix_rrugc_delivery_destination_tenant_active",
            "tenant_id",
            "active",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="google_drive_folder"
    )
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RrugcDeliveryPackageModel(Base):
    __tablename__ = "rrugc_delivery_packages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_delivery_package_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["destination_id"],
            ["rrugc_delivery_destinations.id"],
            name="fk_rrugc_delivery_package_destination",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_delivery_package_key",
        ),
        Index(
            "ix_rrugc_delivery_package_campaign_status",
            "tenant_id",
            "campaign_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_rrugc_delivery_package_expires",
            "tenant_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_rrugc_delivery_package_retry_due",
            "tenant_id",
            "status",
            "next_retry_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    export_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manifest_json: Mapped[list | None] = mapped_column(JSON)
    created_by_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RrugcDeliveryEventModel(Base):
    __tablename__ = "rrugc_delivery_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["rrugc_campaigns.tenant_id", "rrugc_campaigns.id"],
            name="fk_rrugc_delivery_event_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["package_id"],
            ["rrugc_delivery_packages.id"],
            name="fk_rrugc_delivery_event_package",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_rrugc_delivery_event_key",
        ),
        Index(
            "ix_rrugc_delivery_event_tenant_created",
            "tenant_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(36), nullable=False)
    package_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class RrugcDeliveryItemModel(Base):
    __tablename__ = "rrugc_delivery_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["package_id"],
            ["rrugc_delivery_packages.id"],
            name="fk_rrugc_delivery_item_package",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["export_id"],
            ["rrugc_exports.id"],
            name="fk_rrugc_delivery_item_export",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_rrugc_delivery_item_catalog_asset",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "package_id",
            "export_id",
            name="uq_rrugc_delivery_item_export",
        ),
        Index(
            "ix_rrugc_delivery_item_package_status",
            "tenant_id",
            "package_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    package_id: Mapped[str] = mapped_column(String(36), nullable=False)
    export_id: Mapped[str] = mapped_column(String(36), nullable=False)
    catalog_asset_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_remote_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    delivered_remote_file_id: Mapped[str | None] = mapped_column(String(255))
    delivered_web_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
