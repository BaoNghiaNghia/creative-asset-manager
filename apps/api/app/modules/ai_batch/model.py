from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint,
    Index, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

def utcnow(): return datetime.now(timezone.utc)
def new_id(): return str(uuid4())

BATCH_TERMINAL_STATUSES={"completed","partial_failed","failed","expired","cancelled"}
ITEM_TERMINAL_STATUSES={"completed","failed","cancelled"}

class AiBatchJobModel(Base):
    __tablename__="ai_batch_jobs"
    __table_args__=(
        UniqueConstraint("tenant_id","id",name="uq_ai_batch_jobs_tenant_id"),
        UniqueConstraint("tenant_id","submission_key",name="uq_ai_batch_jobs_submission"),
        UniqueConstraint("provider","provider_batch_id",name="uq_ai_batch_jobs_provider_id"),
        ForeignKeyConstraint(["tenant_id","metadata_profile_id"],
            ["metadata_profiles.tenant_id","metadata_profiles.id"],
            ondelete="RESTRICT",name="fk_ai_batch_jobs_tenant_profile"),
        CheckConstraint("status IN ('preparing','submitting','submitted','running','importing','completed','partial_failed','failed','expired','cancelled','ambiguous')",name="ck_ai_batch_jobs_status"),
        CheckConstraint("item_count >= 0 AND completed_count >= 0 AND failed_count >= 0 AND missing_count >= 0",name="ck_ai_batch_jobs_counts"),
        Index("ix_ai_batch_jobs_tenant_status","tenant_id","status","next_poll_at"),
        Index("ix_ai_batch_jobs_tenant_created","tenant_id","created_at"),
        Index("ix_ai_batch_jobs_tenant_provider_status_created","tenant_id","provider","status","created_at"),
    )
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    tenant_id: Mapped[str]=mapped_column(String(255),nullable=False)
    submission_key: Mapped[str]=mapped_column(String(512),nullable=False)
    provider: Mapped[str]=mapped_column(String(100),nullable=False)
    model: Mapped[str]=mapped_column(String(255),nullable=False)
    metadata_profile_id: Mapped[str]=mapped_column(String(36),nullable=False)
    metadata_profile: Mapped[str]=mapped_column(String(255),nullable=False)
    metadata_profile_version: Mapped[str]=mapped_column(String(100),nullable=False)
    prompt_version: Mapped[str]=mapped_column(String(100),nullable=False)
    pipeline_version: Mapped[str]=mapped_column(String(100),nullable=False)
    provider_batch_id: Mapped[str | None]=mapped_column(String(512))
    provider_request_id: Mapped[str | None]=mapped_column(String(255))
    credential_fingerprint: Mapped[str | None]=mapped_column(String(64))
    credential_encrypted_secret: Mapped[str | None]=mapped_column(Text)
    credential_key_version: Mapped[str | None]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(24),nullable=False,default="preparing")
    item_count: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    completed_count: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    failed_count: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    missing_count: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    submission_attempt: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    poll_attempt: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    import_attempt: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    result_cursor: Mapped[str | None]=mapped_column(String(512))
    input_checksum: Mapped[str | None]=mapped_column(String(64))
    input_bytes: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    estimated_cost_micros: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    actual_cost_micros: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    currency: Mapped[str]=mapped_column(String(3),nullable=False,default="USD")
    usage_json: Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
    cancellation_requested: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    next_poll_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None]=mapped_column(String(100))
    last_error_message: Mapped[str | None]=mapped_column(Text)
    error_json: Mapped[dict | None]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
    submitted_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow,onupdate=utcnow)

class AiBatchItemModel(Base):
    __tablename__="ai_batch_items"
    __table_args__=(
        ForeignKeyConstraint(["tenant_id","batch_job_id"],
            ["ai_batch_jobs.tenant_id","ai_batch_jobs.id"],
            ondelete="CASCADE",name="fk_ai_batch_items_tenant_batch"),
        ForeignKeyConstraint(["tenant_id","asset_id"],
            ["assets.tenant_id","assets.id"],ondelete="RESTRICT",
            name="fk_ai_batch_items_tenant_asset"),
        ForeignKeyConstraint(["analysis_id"],["asset_ai_analyses.id"],
            ondelete="RESTRICT",name="fk_ai_batch_items_analysis"),
        UniqueConstraint("batch_job_id","custom_item_id",name="uq_ai_batch_items_custom"),
        UniqueConstraint("batch_job_id","analysis_id",name="uq_ai_batch_items_analysis"),
        UniqueConstraint("tenant_id","analysis_id",name="uq_ai_batch_items_tenant_analysis"),
        CheckConstraint("status IN ('pending','prepared','submitted','completed','failed','missing','cancelled','budget_blocked')",name="ck_ai_batch_items_status"),
        CheckConstraint("attempt_count >= 0",name="ck_ai_batch_items_attempt"),
        Index("ix_ai_batch_items_batch_status","batch_job_id","status","id"),
    )
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=new_id)
    tenant_id: Mapped[str]=mapped_column(String(255),nullable=False)
    batch_job_id: Mapped[str]=mapped_column(String(36),nullable=False)
    custom_item_id: Mapped[str]=mapped_column(String(128),nullable=False)
    asset_id: Mapped[str]=mapped_column(String(36),nullable=False)
    analysis_id: Mapped[str]=mapped_column(String(36),nullable=False)
    provider_item_id: Mapped[str | None]=mapped_column(String(255))
    status: Mapped[str]=mapped_column(String(24),nullable=False,default="pending")
    attempt_count: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    result_received: Mapped[bool]=mapped_column(Boolean,nullable=False,default=False)
    result_sequence: Mapped[int | None]=mapped_column(Integer)
    budget_operation_key: Mapped[str | None]=mapped_column(String(512))
    budget_reservation_id: Mapped[str | None]=mapped_column(String(36))
    estimated_cost_micros: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    actual_cost_micros: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    usage_json: Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
    last_error_code: Mapped[str | None]=mapped_column(String(100))
    last_error_message: Mapped[str | None]=mapped_column(Text)
    error_json: Mapped[dict | None]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow)
    submitted_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=utcnow,onupdate=utcnow)
