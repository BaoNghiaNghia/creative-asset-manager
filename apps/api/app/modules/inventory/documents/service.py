from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.jobs.errors import InventoryJobFailure
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.persistence_model import (
    InventoryAiAnalysisModel, InventoryDocumentModel, InventoryDocumentPageModel,
    InventoryItemAliasModel, InventoryItemModel, InventoryLineModel, InventoryLocationModel,
    InventoryReviewModel, inventory_utcnow,
)

INVENTORY_DOCUMENT_NORMALIZE_JOB = "inventory_document_normalize"
INVENTORY_DOCUMENT_VALIDATE_JOB = "inventory_document_validate"
OUTCOMES = frozenset(("APPROVED", "NEEDS_REVIEW", "NEEDS_REUPLOAD", "REJECTED"))
KNOWN_UNITS = {"g", "kg", "ml", "l", "piece", "pack", "box", "bag", "bottle", "can"}
_UNIT_ALIASES = {"gram": "g", "grams": "g", "kg": "kg", "kilogram": "kg", "ml": "ml", "l": "l", "litre": "l", "cai": "piece", "cái": "piece", "goi": "pack", "gói": "pack", "hop": "box", "hộp": "box", "chai": "bottle", "lon": "can"}

class InventoryBusinessFailure(InventoryJobFailure):
    pass

def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text).casefold()

def normalize_unit(value: object) -> str | None:
    unit = normalize_text(value)
    return _UNIT_ALIASES.get(unit, unit if unit in KNOWN_UNITS else None)

def decimal_value(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

@dataclass(frozen=True)
class Resolution:
    item: InventoryItemModel | None
    reason: str | None

class InventoryDocumentNormalizer:
    def __init__(self, session_factory: sessionmaker): self.session_factory = session_factory

    def execute(self, job: InventoryJobModel) -> None:
        with self.session_factory() as session:
            analysis = session.scalar(select(InventoryAiAnalysisModel).where(InventoryAiAnalysisModel.tenant_id == job.tenant_id, InventoryAiAnalysisModel.id == job.entity_id))
            if analysis is None: raise InventoryBusinessFailure("inventory_normalize_analysis_not_found", retryable=False)
            if analysis.status != "succeeded": raise InventoryBusinessFailure("inventory_normalize_analysis_not_ready", retryable=True)
            document = session.scalar(select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == job.tenant_id, InventoryDocumentModel.id == analysis.document_id))
            page = session.scalar(select(InventoryDocumentPageModel).where(InventoryDocumentPageModel.tenant_id == job.tenant_id, InventoryDocumentPageModel.id == analysis.page_id))
            if document is None or page is None: raise InventoryBusinessFailure("inventory_normalize_parent_not_found", retryable=False)
            lines = list((analysis.extracted_json or {}).get("raw_item_lines") or (analysis.extracted_json or {}).get("lines") or [])
            for number, raw in enumerate(lines, 1): self._line(session, job.tenant_id, document, page, analysis, number, raw)
            analysis.normalized_result_json = {"normalized_at": inventory_utcnow().isoformat(), "line_count": len(lines)}
            document.status = "validating"; page.analysis_status = "completed"
            repository = InventoryJobRepository(session, (INVENTORY_DOCUMENT_NORMALIZE_JOB, INVENTORY_DOCUMENT_VALIDATE_JOB))
            repository.create_job(tenant_id=job.tenant_id, job_type=INVENTORY_DOCUMENT_VALIDATE_JOB, entity_type="inventory_document", entity_id=document.id, idempotency_key=f"inventory-document-validate:{document.id}:{analysis.id}", payload={"document_id": document.id})
            session.commit()

    def _resolve(self, session: Session, tenant_id: str, raw_name: str) -> Resolution:
        key = normalize_text(raw_name)
        direct = list(session.scalars(select(InventoryItemModel).where(InventoryItemModel.tenant_id == tenant_id, InventoryItemModel.active.is_(True), func.lower(InventoryItemModel.name) == key)))
        aliases = list(session.scalars(select(InventoryItemAliasModel).where(InventoryItemAliasModel.tenant_id == tenant_id, InventoryItemAliasModel.normalized_alias == key)))
        ids = {row.id for row in direct} | {row.item_id for row in aliases}
        if len(ids) != 1: return Resolution(None, "unknown_item" if not ids else "ambiguous_item")
        item = session.scalar(select(InventoryItemModel).where(InventoryItemModel.tenant_id == tenant_id, InventoryItemModel.id == next(iter(ids)), InventoryItemModel.active.is_(True)))
        return Resolution(item, None if item is not None else "unknown_item")

    def _line(self, session, tenant_id, document, page, analysis, number, raw):
        existing = session.scalar(select(InventoryLineModel).where(InventoryLineModel.tenant_id == tenant_id, InventoryLineModel.analysis_id == analysis.id, InventoryLineModel.line_number == number))
        if existing is not None: return existing
        raw_name = str(raw.get("raw_item_name") or "").strip()
        resolution = self._resolve(session, tenant_id, raw_name)
        whole, fraction, waste = decimal_value(raw.get("whole_quantity")), decimal_value(raw.get("fraction_quantity")), decimal_value(raw.get("waste_quantity"))
        whole_unit, fraction_unit = normalize_unit(raw.get("whole_unit")), normalize_unit(raw.get("fraction_unit"))
        reasons = [reason for reason in (resolution.reason, None if whole is None or whole >= 0 else "negative_quantity", None if fraction is None or fraction >= 0 else "negative_quantity", None if waste is None or waste >= 0 else "negative_waste", None if raw.get("whole_unit") in (None, "") or whole_unit else "unknown_unit", None if raw.get("fraction_unit") in (None, "") or fraction_unit else "unknown_fraction_unit", None if not waste or waste == 0 or raw.get("waste_reason") else "waste_reason_required") if reason]
        item = resolution.item
        conversion = item.conversion_factor if item is not None else None
        base = None if item is None else (whole or Decimal(0)) * conversion + (fraction or Decimal(0))
        line = InventoryLineModel(tenant_id=tenant_id, document_id=document.id, page_id=page.id, analysis_id=analysis.id, line_number=number, raw_item_name=raw_name, item_id=item.id if item else None, raw_values_json=dict(raw), normalized_values_json={"normalized_item_name": normalize_text(raw_name), "reason_codes": reasons, "whole_unit": whole_unit, "fraction_unit": fraction_unit}, whole_quantity=whole, fraction_quantity=fraction, whole_unit=whole_unit, fraction_unit=fraction_unit, waste_quantity=waste, waste_reason=raw.get("waste_reason"), conversion_factor_snapshot=conversion, quantity_base_unit=base, confidence=decimal_value(raw.get("confidence")), validation_status="needs_review" if reasons else "unvalidated", review_note=", ".join(reasons) or None)
        # Concurrent workers may normalize the same analysis.  The database
        # unique constraint is authoritative; contain a conflict in a savepoint
        # and return the already-created line without poisoning this transaction.
        try:
            with session.begin_nested():
                session.add(line)
                session.flush()
            return line
        except IntegrityError:
            existing = session.scalar(select(InventoryLineModel).where(
                InventoryLineModel.tenant_id == tenant_id,
                InventoryLineModel.analysis_id == analysis.id,
                InventoryLineModel.line_number == number,
            ))
            if existing is None:
                raise
            return existing

class InventoryDocumentValidator:
    def __init__(self, session_factory: sessionmaker): self.session_factory = session_factory
    def execute(self, job: InventoryJobModel) -> None:
        document_id = str((job.payload_json or {}).get("document_id") or job.entity_id)
        with self.session_factory() as session:
            document = session.scalar(select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == job.tenant_id, InventoryDocumentModel.id == document_id).with_for_update() if session.bind.dialect.name == "postgresql" else select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == job.tenant_id, InventoryDocumentModel.id == document_id))
            if document is None: raise InventoryBusinessFailure("inventory_validate_document_not_found", retryable=False)
            lines = list(session.scalars(select(InventoryLineModel).where(InventoryLineModel.tenant_id == job.tenant_id, InventoryLineModel.document_id == document.id).order_by(InventoryLineModel.line_number)))
            reasons = []
            if document.expected_pages and document.received_pages < document.expected_pages: reasons.append("missing_required_page")
            duplicate_pages = session.scalar(select(func.count(InventoryDocumentPageModel.id)).where(InventoryDocumentPageModel.tenant_id == job.tenant_id, InventoryDocumentPageModel.document_id == document.id, InventoryDocumentPageModel.preparation_status == "duplicate")) or 0
            if duplicate_pages: reasons.append("duplicate_submission")
            if not lines: reasons.append("missing_lines")
            for line in lines:
                reasons.extend((line.normalized_values_json or {}).get("reason_codes") or [])
                if line.confidence is None or line.confidence < Decimal("0.80"): reasons.append("low_confidence")
            outcome = "APPROVED" if not reasons else ("NEEDS_REUPLOAD" if "missing_required_page" in reasons else "NEEDS_REVIEW")
            document.status = outcome.lower()
            for line in lines:
                line.validation_status = "valid" if outcome == "APPROVED" else "needs_review"
            if outcome != "APPROVED":
                self._reviews(session, job.tenant_id, document, lines, sorted(set(reasons)))
            session.commit()

    def _reviews(self, session, tenant_id, document, lines, reasons):
        targets = lines or [None]
        for line in targets:
            for reason in reasons:
                key = f"inventory-review:{document.id}:{line.id if line else 'document'}:{reason}"
                existing = session.scalar(select(InventoryReviewModel).where(InventoryReviewModel.tenant_id == tenant_id, InventoryReviewModel.idempotency_key == key))
                if existing is None:
                    session.add(InventoryReviewModel(tenant_id=tenant_id, document_id=document.id, line_id=line.id if line else None, idempotency_key=key, reason_code=reason, original_value_json=dict(line.raw_values_json) if line else {}, suggested_value_json=dict(line.normalized_values_json) if line else {"document_id": document.id}))
