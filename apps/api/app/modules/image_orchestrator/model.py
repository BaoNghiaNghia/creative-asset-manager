"""P1 additive, image-only persistence. No video queue foreign keys.

History survives an unpick or artwork replacement. Admission (Picked,
current source SHA and artwork QA) is checked by the future service.
"""
from sqlalchemy import (Boolean, CheckConstraint, ForeignKeyConstraint, Index,
                        Integer, BigInteger, JSON, String, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ImageFlowControl(Base):
    __tablename__ = "cam_image_flow_controls"
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    running: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ImageBatch(Base):
    __tablename__ = "cam_image_batches"
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pack_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    release_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ImagePack(Base):
    __tablename__ = "cam_image_packs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "listing_key", "artwork_sha256", "garment_sku",
                         "pack_revision", name="uq_cam_image_pack_identity"),
        ForeignKeyConstraint(["tenant_id", "batch_id"],
                             ["cam_image_batches.tenant_id", "cam_image_batches.batch_id"],
                             name="fk_cam_image_pack_batch"),
        CheckConstraint("pack_revision BETWEEN 1 AND 10000", name="ck_cam_image_pack_revision"),
        CheckConstraint("epoch >= 0", name="ck_cam_image_pack_epoch"),
        CheckConstraint("garment_sku IN ('C1717','G500','G180','G185')",
                        name="ck_cam_image_pack_sku"),
        Index("ix_cam_image_pack_batch", "tenant_id", "batch_id", "pack_id"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    listing_key: Mapped[str] = mapped_column(String(150), nullable=False)
    artwork_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    garment_sku: Mapped[str] = mapped_column(String(10), nullable=False)
    pack_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    placement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Full approved immutable plan. Older P1 test packs may have no plan.
    approved_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128))
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    wave_claims: Mapped[dict] = mapped_column(JSON, nullable=False)
    ack_state: Mapped[dict] = mapped_column(JSON, nullable=False)
    used_attempt_ids: Mapped[list] = mapped_column(JSON, nullable=False)


class ImageRole(Base):
    __tablename__ = "cam_image_roles"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "pack_id"],
                             ["cam_image_packs.tenant_id", "cam_image_packs.pack_id"],
                             name="fk_cam_image_role_pack"),
        CheckConstraint("role BETWEEN 1 AND 13", name="ck_cam_image_role_number"),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_cam_image_role_attempts"),
        CheckConstraint("status IN ('pending','dispatch_uncertain','upload_pending',"
                        "'reconcile_required','verified','hold')",
                        name="ck_cam_image_role_status"),
        Index("ix_cam_image_role_status", "tenant_id", "status", "pack_id"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_id: Mapped[str | None] = mapped_column(String(128))
    receipt_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(128))

class ImageCommand(Base):
    """Stable command identity per pack/wave. Lease secrets remain in the pack."""
    __tablename__ = "cam_image_commands"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "pack_id"],
                             ["cam_image_packs.tenant_id", "cam_image_packs.pack_id"],
                             name="fk_cam_image_command_pack"),
        UniqueConstraint("tenant_id", "pack_id", "wave", name="uq_cam_image_command_wave"),
        CheckConstraint("wave IN (1,2)", name="ck_cam_image_command_wave"),
        CheckConstraint("status IN ('pending','claimed','completed','hold')",
                        name="ck_cam_image_command_status"),
        Index("ix_cam_image_command_status", "tenant_id", "status", "command_id"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    wave: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    claim_epoch: Mapped[int | None] = mapped_column(Integer)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    resource_epoch: Mapped[int | None] = mapped_column(Integer)
    image_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ImageCapabilityState(Base):
    """Image-only policy attached to an already authenticated worker identity.

    Not a credential registry. No worker may self-approve this state.
    """
    __tablename__ = "cam_image_capability_states"
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    claim_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skill_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cooldown_until: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ImageUploadIntent(Base):
    """One immutable PNG digest per attempt; persisted before storage I/O."""
    __tablename__ = "cam_image_upload_intents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "pack_id", "role"],
                             ["cam_image_roles.tenant_id", "cam_image_roles.pack_id",
                              "cam_image_roles.role"], name="fk_cam_image_upload_role"),
        CheckConstraint("width BETWEEN 1600 AND 4096 AND width = height",
                        name="ck_cam_image_upload_dimensions"),
        CheckConstraint("status IN ('pending','verified','reconcile_required')",
                        name="ck_cam_image_upload_status"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    qa_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")

class ImagePlanApproval(Base):
    """Trusted CAM admission evidence; never written by a renderer bearer."""
    __tablename__ = "cam_image_plan_approvals"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id","pack_id"],
            ["cam_image_packs.tenant_id","cam_image_packs.pack_id"],
            name="fk_cam_image_plan_approval_pack"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ImageQAApproval(Base):
    """Immutable reviewer evidence bound to a single rendered attempt."""
    __tablename__ = "cam_image_qa_approvals"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id","pack_id","role"],
            ["cam_image_roles.tenant_id","cam_image_roles.pack_id","cam_image_roles.role"],
            name="fk_cam_image_qa_approval_role"),
        UniqueConstraint("tenant_id","pack_id","role","attempt_id","image_sha256",
                         name="uq_cam_image_qa_attempt_image"),
        CheckConstraint("role BETWEEN 1 AND 13",name="ck_cam_image_qa_role"),
        CheckConstraint("verdict IN ('pass','hold','unverified')",name="ck_cam_image_qa_verdict"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
