"""Immutable V6.1 runner QA submissions; not independent approval.

The worker provides the combined QA record, not proof that the PNG passed
review. Only the trusted CAM reviewer may create an ImageQAApproval.
"""
from __future__ import annotations

import json
from sqlalchemy import (BigInteger, CheckConstraint, ForeignKeyConstraint,
                        Integer, JSON, String, UniqueConstraint, select)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from . import policy as p


class ImageQARecordSubmission(Base):
    __tablename__ = "cam_image_qa_record_submissions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id","pack_id","role"],
            ["cam_image_roles.tenant_id","cam_image_roles.pack_id","cam_image_roles.role"],
            name="fk_cam_image_qa_record_role"),
        UniqueConstraint("tenant_id","pack_id","role","attempt_id",
            name="uq_cam_image_qa_record_attempt"),
        CheckConstraint("role BETWEEN 1 AND 13",name="ck_cam_image_qa_record_role"),
        CheckConstraint("epoch >= 1",name="ck_cam_image_qa_record_epoch"),
    )
    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(32), nullable=False)
    record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    record: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


def record_hash(record, role):
    p.require(isinstance(record, dict) and record and type(record.get("role")) is int
              and record["role"] == role, "qa_record_role_mismatch")
    try:
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError,TypeError,RecursionError):
        raise p.Conflict("qa_record_invalid") from None
    p.require(len(raw.encode()) <= 65536,"qa_record_too_large")
    return p.digest(raw)


def require_submission(db, pack, role, attempt, epoch, receipt_id, sha, batch_id=None):
    row=db.scalar(select(ImageQARecordSubmission).where(
        ImageQARecordSubmission.tenant_id==pack.tenant_id,
        ImageQARecordSubmission.receipt_id==receipt_id).with_for_update())
    p.require(row is not None and row.pack_id==pack.pack_id and row.role==role
        and row.attempt_id==attempt and row.epoch==epoch and row.worker_id==pack.owner
        and row.plan_sha256==pack.plan_sha256 and row.record_sha256==sha
        and record_hash(row.record,role)==sha
        and (batch_id is None or row.batch_id==batch_id),"qa_record_not_received")
    return row


def receive(svc, sc, ts, *, role, epoch, token, attempt, batch_id, sha, record):
    """Call only within the existing fenced command transition."""
    from .adaptive_v61 import qa_reservation, require_upload_binding
    p.require(p.valid_sha(sha) and record_hash(record,role)==sha,"qa_record_sha_mismatch")
    row,resource,_=svc._lock_existing(sc)
    def apply(pack,_):
        cmd=svc._command(sc);svc._fence(cmd,resource,epoch,ts)
        lease=p.authorized(pack,svc.worker,epoch,token,ts)
        p.require(cmd.image_granted and lease.adaptive_batches
                  and pack.roles[role].status in ("dispatch_uncertain","upload_pending"),
                  "qa_record_attempt_not_active")
        svc.db.refresh(row)
        p.require(svc.runtime.authorize_plan(svc.db,svc.identity,row),"image_plan_unverified")
        rid=qa_reservation(pack,role,attempt,row.plan_sha256)
        require_upload_binding(pack,lease,role=role,attempt=attempt,batch_id=batch_id,
            output_index=0,method="native_imagegen",qa_id=rid,qa_record_sha=sha,
            plan_sha=row.plan_sha256)
        old=svc.db.get(ImageQARecordSubmission,(svc.tenant,rid))
        if old is not None:
            require_submission(svc.db,row,role,attempt,epoch,rid,sha,batch_id)
        else:
            svc.db.add(ImageQARecordSubmission(tenant_id=svc.tenant,receipt_id=rid,
                pack_id=sc.pack_id,role=role,attempt_id=attempt,epoch=epoch,
                worker_id=svc.worker,plan_sha256=row.plan_sha256,batch_id=batch_id,
                record_sha256=sha,record=record,received_at=ts))
            svc.db.flush()
        return {"status":"qa_record_received","qaRecordSha256":sha}
    return svc._transition(sc,row,apply)


def storage_receipt_id(pack, row):
    """Stable identifier for a persisted, independently verified receipt.

    Caller must first verify the stored receipt with central_read_api._receipt.
    No storage receipt is issued from a QA record or HTTP upload ack alone.
    """
    p.require(row.status=="verified" and isinstance(row.receipt_state,dict)
              and row.receipt_state.get("storage_verified") is True
              and isinstance(row.attempt_id,str),"storage_receipt_unverified")
    return p.canonical_sha(["CAM_IMAGE_STORAGE_RECEIPT_V1",pack.tenant_id,
                            pack.pack_id,row.role,row.attempt_id,row.receipt_state])
