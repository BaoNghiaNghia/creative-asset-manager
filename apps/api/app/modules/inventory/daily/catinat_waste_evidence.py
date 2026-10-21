"""Catinat witnessed disposal and manager countersign; NO inventory or Sheets posting.

Two independent human attestations are source-bound evidence only. Stock ledger,
waste quantity depletion and safe carry-forward are separate unavailable steps.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import re

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_approvals import _fingerprint
from app.modules.inventory.daily.catinat_batch_evidence import (
    CatinatBatchBlocked, _decimal, _note, _source_row, _time,
)
from app.modules.inventory.daily.catinat_count_confirmations import _source
from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalRequestModel, InventoryCatinatBatchEvidenceModel,
    InventoryCatinatWasteWitnessModel, InventoryCatinatWasteCertificationModel,
    inventory_utcnow,
)

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")


def _blocked(code: str):
    raise CatinatBatchBlocked(code)


def _view(w: InventoryCatinatWasteWitnessModel, cert=None, *, replay=False,
          source_current: bool = False) -> dict:
    return {
        "id": w.id, "approval_request_id": w.approval_request_id,
        "batch_evidence_id": w.batch_evidence_id,
        "business_date": w.business_date.isoformat(),
        "source_hash": w.source_hash, "source_current": source_current,
        "quantity": format(w.quantity, "f"), "unit": w.unit,
        "observed_at": w.observed_at.isoformat(), "method": w.method,
        "evidence_reference": w.evidence_reference,
        "note": w.note, "witnessed_by": w.witnessed_by,
        "witnessed_at": w.witnessed_at.isoformat() if w.witnessed_at else None,
        "status": w.status,
        "manager_certified_by": cert.certified_by if cert else None,
        "manager_certified_at": cert.certified_at.isoformat() if cert else None,
        "manager_certification_note": cert.note if cert else None,
        "physical_evidence_countersigned": bool(cert),
        "ledger_posted": False, "inventory_writes": 0, "sheets_writes": 0,
        "ready_to_sign": False, "automatic_carry_forward_allowed": False,
        "idempotent_replay": replay,
    }


class CatinatWasteEvidenceService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def witness(
        self, *, tenant_id: str, actor_id: str, approval_request_id: str,
        business_date: date, expected_source_hash: str,
        idempotency_key: str, quantity: str, observed_at: datetime,
        method: str, evidence_reference: str, note: str, current_readiness,
    ) -> dict:
        if not tenant_id or not actor_id or not approval_request_id or not _KEY.fullmatch(
                str(idempotency_key or "")):
            _blocked("waste_witness_identity_required")
        amount = _decimal(quantity)
        observed = _time(observed_at, required=True)
        if observed > datetime.now(timezone.utc):
            _blocked("waste_observation_in_future")
        action = _note(method)
        if len(action) > 100:
            _blocked("waste_method_too_long")
        reference = str(evidence_reference or "").strip()
        if not 8 <= len(reference) <= 2048 or any(ord(c) < 32 for c in reference):
            _blocked("physical_disposal_document_required")
        explanation = _note(note)
        values = {
            "tenant_id": tenant_id, "approval_request_id": approval_request_id,
            "quantity": format(amount, "f"), "observed_at": observed.isoformat(),
            "method": action, "evidence_reference": reference, "note": explanation,
        }
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.sessions() as s:
            try:
                with s.begin():
                    if s.bind is not None and s.bind.dialect.name == "postgresql":
                        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock))"), {
                            "lock": f"catinat-waste:{tenant_id}:{approval_request_id}",
                        })
                    approval = s.scalar(select(InventoryCatinatApprovalRequestModel).where(
                        InventoryCatinatApprovalRequestModel.tenant_id == tenant_id,
                        InventoryCatinatApprovalRequestModel.id == approval_request_id,
                    ))
                    if approval is None:
                        _blocked("waste_approval_not_found")
                    if approval.kind != "waste_disposal" or approval.status != "approved":
                        _blocked("approved_waste_request_required")
                    if not approval.decided_by or actor_id in {
                        approval.requested_by, approval.decided_by,
                    }:
                        _blocked("independent_physical_witness_required")
                    if approval.business_date != business_date:
                        _blocked("waste_business_date_mismatch")
                    if approval.proposal_hash != _fingerprint(
                        approval.business_date, approval.kind, approval.subject_ref,
                        approval.payload_json, approval.reason,
                    ):
                        _blocked("waste_approval_payload_tampered")
                    proposal = approval.payload_json
                    batch_id = proposal.get("batch_evidence_id")
                    if not batch_id or approval.subject_ref != batch_id:
                        _blocked("waste_batch_reference_mismatch")
                    batch = s.scalar(select(InventoryCatinatBatchEvidenceModel).where(
                        InventoryCatinatBatchEvidenceModel.tenant_id == tenant_id,
                        InventoryCatinatBatchEvidenceModel.id == batch_id,
                        InventoryCatinatBatchEvidenceModel.business_date == business_date,
                    ))
                    if batch is None:
                        _blocked("waste_batch_not_found")
                    if actor_id == batch.attested_by:
                        _blocked("independent_physical_witness_required")
                    if (proposal.get("quantity_requested") != format(amount, "f")
                        or proposal.get("unit") != batch.unit
                        or proposal.get("sheet") != batch.sheet_name
                        or proposal.get("cell") != batch.cell_ref
                        or proposal.get("material") != batch.material_display
                        or proposal.get("lot_reference") != batch.lot_reference
                        or proposal.get("source_hash") != batch.source_hash
                        or proposal.get("disposal_physically_verified") is not False
                        or proposal.get("ledger_posted") is not False
                        or proposal.get("sheets_writes") != 0):
                        _blocked("waste_approval_batch_or_quantity_mismatch")
                    digest, workbook, row = _source_row(
                        current_readiness(), business_date=business_date,
                        sheet=batch.sheet_name, cell=batch.cell_ref,
                        expected_hash=expected_source_hash,
                    )
                    if (digest != batch.source_hash or workbook != batch.spreadsheet_file_id
                        or row["material"] != batch.material_display
                        or row["unit"] != batch.unit or row["form"] != batch.form):
                        _blocked("waste_source_changed")
                    batch_time = batch.event_at
                    if batch_time.tzinfo is None:  # SQLite strips offset in tests.
                        batch_time = batch_time.replace(tzinfo=timezone.utc)
                    if observed < batch_time:
                        _blocked("disposal_before_batch_event")
                    existing = s.scalar(select(InventoryCatinatWasteWitnessModel).where(
                        InventoryCatinatWasteWitnessModel.tenant_id == tenant_id,
                        InventoryCatinatWasteWitnessModel.approval_request_id == approval_request_id,
                    ))
                    if existing:
                        if (existing.witnessed_by != actor_id or
                                existing.idempotency_key != idempotency_key or
                                existing.evidence_hash != fingerprint):
                            _blocked("waste_witness_conflicting_replay")
                        cert = s.scalar(select(InventoryCatinatWasteCertificationModel).where(
                            InventoryCatinatWasteCertificationModel.tenant_id == tenant_id,
                            InventoryCatinatWasteCertificationModel.witness_id == existing.id,
                        ))
                        return _view(existing, cert, replay=True, source_current=True)
                    clash = s.scalar(select(InventoryCatinatWasteWitnessModel).where(
                        InventoryCatinatWasteWitnessModel.tenant_id == tenant_id,
                        InventoryCatinatWasteWitnessModel.idempotency_key == idempotency_key,
                    ))
                    if clash:
                        _blocked("waste_witness_idempotency_conflict")
                    record = InventoryCatinatWasteWitnessModel(
                        tenant_id=tenant_id, approval_request_id=approval_request_id,
                        batch_evidence_id=batch_id, idempotency_key=idempotency_key,
                        business_date=business_date, source_hash=digest, quantity=amount,
                        unit=batch.unit, observed_at=observed, method=action,
                        evidence_reference=reference, note=explanation,
                        witnessed_by=actor_id, witnessed_at=inventory_utcnow(),
                        evidence_hash=fingerprint, status="staff_witnessed_unposted",
                    )
                    s.add(record)
                    s.flush()
                    return _view(record, source_current=True)
            except IntegrityError as exc:
                raise CatinatBatchBlocked("waste_witness_concurrency_conflict") from exc

    def countersign(self, *, tenant_id: str, actor_id: str, witness_id: str,
                    business_date: date, expected_source_hash: str,
                    note: str, current_readiness) -> dict:
        if not tenant_id or not actor_id or not witness_id:
            _blocked("waste_certifier_identity_required")
        why = _note(note)
        with self.sessions() as s:
            try:
                with s.begin():
                    if s.bind is not None and s.bind.dialect.name == "postgresql":
                        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock))"), {
                            "lock": f"catinat-waste-cert:{tenant_id}:{witness_id}",
                        })
                    witness = s.scalar(select(InventoryCatinatWasteWitnessModel).where(
                        InventoryCatinatWasteWitnessModel.tenant_id == tenant_id,
                        InventoryCatinatWasteWitnessModel.id == witness_id,
                    ))
                    if witness is None:
                        _blocked("waste_witness_not_found")
                    approval = s.scalar(select(InventoryCatinatApprovalRequestModel).where(
                        InventoryCatinatApprovalRequestModel.tenant_id == tenant_id,
                        InventoryCatinatApprovalRequestModel.id == witness.approval_request_id,
                    ))
                    if (approval is None or approval.status != "approved"
                            or approval.kind != "waste_disposal"
                            or actor_id != approval.decided_by
                            or actor_id in {witness.witnessed_by, approval.requested_by}):
                        _blocked("approved_manager_countersign_required")
                    if approval.proposal_hash != _fingerprint(
                        approval.business_date, approval.kind, approval.subject_ref,
                        approval.payload_json, approval.reason,
                    ):
                        _blocked("waste_approval_payload_tampered")
                    if (business_date != witness.business_date
                            or approval.business_date != business_date
                            or approval.payload_json.get("batch_evidence_id") != witness.batch_evidence_id
                            or (approval.payload_json.get("quantity_requested") is None)
                            or _decimal(approval.payload_json["quantity_requested"]) != witness.quantity):
                        _blocked("waste_business_date_or_quantity_mismatch")
                    batch = s.scalar(select(InventoryCatinatBatchEvidenceModel).where(
                        InventoryCatinatBatchEvidenceModel.tenant_id == tenant_id,
                        InventoryCatinatBatchEvidenceModel.id == witness.batch_evidence_id,
                    ))
                    if batch is None:
                        _blocked("waste_batch_not_found")
                    digest, workbook, row = _source_row(
                        current_readiness(), business_date=business_date,
                        sheet=batch.sheet_name, cell=batch.cell_ref,
                        expected_hash=expected_source_hash,
                    )
                    if (digest != witness.source_hash or workbook != batch.spreadsheet_file_id
                            or row["material"] != batch.material_display
                            or row["form"] != batch.form or row["unit"] != batch.unit):
                        _blocked("waste_source_changed")
                    certification = s.scalar(select(InventoryCatinatWasteCertificationModel).where(
                        InventoryCatinatWasteCertificationModel.tenant_id == tenant_id,
                        InventoryCatinatWasteCertificationModel.witness_id == witness_id,
                    ))
                    if certification:
                        if certification.certified_by != actor_id or certification.note != why:
                            _blocked("waste_countersign_conflicting_replay")
                        return _view(witness, certification, replay=True, source_current=True)
                    certification = InventoryCatinatWasteCertificationModel(
                        tenant_id=tenant_id, witness_id=witness.id,
                        business_date=business_date, approved_source_hash=digest,
                        certified_by=actor_id, note=why,
                        certified_at=inventory_utcnow(), status="countersigned_unposted",
                    )
                    s.add(certification)
                    s.flush()
                    return _view(witness, certification, source_current=True)
            except IntegrityError as exc:
                raise CatinatBatchBlocked("waste_countersign_concurrency_conflict") from exc

    def list(self, *, tenant_id: str, business_date: date,
             current_readiness) -> dict:
        digest, workbook = _source(current_readiness(), business_date)
        with self.sessions() as s:
            witnesses = s.scalars(select(InventoryCatinatWasteWitnessModel).where(
                InventoryCatinatWasteWitnessModel.tenant_id == tenant_id,
                InventoryCatinatWasteWitnessModel.business_date == business_date,
            ).order_by(InventoryCatinatWasteWitnessModel.witnessed_at.desc()).limit(200)).all()
            ids = [w.id for w in witnesses]
            certificates = s.scalars(select(InventoryCatinatWasteCertificationModel).where(
                InventoryCatinatWasteCertificationModel.tenant_id == tenant_id,
                InventoryCatinatWasteCertificationModel.witness_id.in_(ids),
            )).all() if ids else []
            by_witness = {c.witness_id: c for c in certificates}
            return {
                "source_hash": digest, "business_date": business_date.isoformat(),
                "items": [_view(w, by_witness.get(w.id),
                                source_current=w.source_hash == digest)
                          for w in witnesses],
                "mode": "read_only_waste_evidence_unposted",
                "stock_writes": 0, "sheets_writes": 0,
                "ready_to_sign": False, "automatic_carry_forward_allowed": False,
            }
