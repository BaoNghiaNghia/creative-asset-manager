"""Catinat immutable manager mapping evidence; never enables stock/Sheets writes."""
from __future__ import annotations

from datetime import date
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.daily.catinat_approvals import _fingerprint
from app.modules.inventory.daily.catinat_count_confirmations import _source, CatinatCountBlocked
from app.modules.inventory.daily.catinat_catalog_mapping import _unit_match, _normal
from app.modules.inventory.persistence_model import (
    InventoryCatinatApprovalRequestModel,
    InventoryCatinatCatalogCertificationModel,
    InventoryItemModel,
    InventoryLocationModel,
    inventory_utcnow,
)


class CatinatCertificationBlocked(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _public(row: InventoryCatinatCatalogCertificationModel, *, replay=False) -> dict:
    return {
        "id": row.id, "version": row.version,
        "business_date": row.business_date.isoformat(),
        "sheet": row.sheet_name, "cell": row.cell_ref, "source_hash": row.source_hash,
        "material": row.material_display, "form": row.form, "unit": row.unit,
        "item_id": row.item_id, "location_id": row.location_id,
        "approval_request_id": row.approval_request_id,
        "approved_by": row.approved_by, "certified_by": row.certified_by,
        "certification_note": row.certification_note,
        "certified_at": row.certified_at.isoformat() if row.certified_at else None,
        "status": "evidence_only", "mapping_activated": False,
        "ready_to_sign": False, "automatic_carry_forward_allowed": False,
        "inventory_writes": 0, "sheets_writes": 0,
        "idempotent_replay": replay,
    }


def _exact_proposal(proposal: dict, *, report: dict, row: dict, location: dict) -> bool:
    source_fields = {
        "source_hash": report["source_hash"],
        "source_sheet": row["sheet"], "source_cell": row["cell"],
        "material_display": row["material"],
        "source_form": row["form"], "source_unit": row["unit"],
        "matching_status": row["status"],
        "candidate_item_ids": row["candidate_item_ids"],
        "candidate_location_ids": location["candidate_location_ids"],
        "mapping_certified": False, "inventory_write_performed": False,
        "sheets_writes": 0,
    }
    return (
        isinstance(proposal, dict)
        and set(proposal) == set(source_fields)
        and all(type(proposal[field]) is type(value) and proposal[field] == value
                for field, value in source_fields.items())
    )


class CatinatCatalogCertificationService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def certify(
        self, *, tenant_id: str, actor_id: str, request_id: str,
        item_id: str, location_id: str, note: str,
        current_readiness: Callable[[date], dict],
        current_preview: Callable[[date], dict],
    ) -> dict:
        if not tenant_id or not actor_id or not request_id or not item_id or not location_id:
            raise CatinatCertificationBlocked("missing_certification_identity")
        if not isinstance(note, str) or not 8 <= len(note.strip()) <= 1000:
            raise CatinatCertificationBlocked("certification_note_required")
        # Read the approval's date and immutable payload first; never accept
        # a client-provided date or an arbitrary workbook/tenant identifier.
        with self.sessions() as s:
            approval = s.scalar(select(InventoryCatinatApprovalRequestModel).where(
                InventoryCatinatApprovalRequestModel.tenant_id == tenant_id,
                InventoryCatinatApprovalRequestModel.id == request_id,
            ))
            if approval is None:
                raise CatinatCertificationBlocked("approval_not_found")
            business_date = approval.business_date

        try:
            ready = current_readiness(business_date)
            digest, spreadsheet = _source(ready, business_date)
            report = current_preview(business_date)
        except CatinatCountBlocked as exc:
            raise CatinatCertificationBlocked(exc.code) from exc
        if (report.get("business_date") != business_date.isoformat()
                or report.get("source_hash") != digest
                or report.get("spreadsheet_id") != spreadsheet):
            raise CatinatCertificationBlocked("mapping_source_changed")
        if report.get("mode") != "read_only_uncertified_catalog_candidates" or not isinstance(report.get("items"), list):
            raise CatinatCertificationBlocked("mapping_preview_untrusted")
        with self.sessions() as s:
            try:
                with s.begin():
                    if s.bind is not None and s.bind.dialect.name == "postgresql":
                        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
                            "key": f"catinat-certify:{tenant_id}:{request_id}",
                        })
                    approval = s.scalar(select(InventoryCatinatApprovalRequestModel).where(
                        InventoryCatinatApprovalRequestModel.tenant_id == tenant_id,
                        InventoryCatinatApprovalRequestModel.id == request_id,
                    ))
                    if approval is None:
                        raise CatinatCertificationBlocked("approval_not_found")
                    if approval.kind != "catalog_mapping" or approval.status != "approved":
                        raise CatinatCertificationBlocked("approved_catalog_request_required")
                    if approval.requested_by == actor_id or approval.decided_by != actor_id:
                        raise CatinatCertificationBlocked("certifying_manager_mismatch")
                    existing = s.scalar(select(InventoryCatinatCatalogCertificationModel).where(
                        InventoryCatinatCatalogCertificationModel.tenant_id == tenant_id,
                        InventoryCatinatCatalogCertificationModel.approval_request_id == request_id,
                    ))
                    if existing:
                        if (existing.item_id == item_id and existing.location_id == location_id
                                and existing.certified_by == actor_id
                                and existing.certification_note == note.strip()
                                and existing.source_hash == digest):
                            return _public(existing, replay=True)
                        raise CatinatCertificationBlocked("certification_conflicting_replay")
                    proposal = approval.payload_json
                    if approval.proposal_hash != _fingerprint(
                        approval.business_date, approval.kind, approval.subject_ref,
                        proposal, approval.reason,
                    ):
                        raise CatinatCertificationBlocked("approval_evidence_tampered")
                    if proposal.get("source_hash") != digest:
                        raise CatinatCertificationBlocked("mapping_source_changed")
                    sheet = proposal.get("source_sheet")
                    cell = proposal.get("source_cell")
                    if approval.subject_ref != f"{sheet}|{cell}":
                        raise CatinatCertificationBlocked("approval_subject_mismatch")
                    rows = [r for r in report["items"] if r.get("sheet") == sheet and r.get("cell") == cell]
                    places = [p for p in report.get("locations", []) if p.get("sheet") == sheet]
                    if len(rows) != 1 or len(places) != 1:
                        raise CatinatCertificationBlocked("mapping_candidate_unavailable")
                    row, place = rows[0], places[0]
                    if (row["status"] != "exact_name_and_unit_candidate"
                            or place["status"] != "exact_name_candidate"
                            or row.get("candidate_item_ids") != [item_id]
                            or place.get("candidate_location_ids") != [location_id]
                            or not _exact_proposal(proposal, report=report, row=row, location=place)):
                        raise CatinatCertificationBlocked("mapping_candidate_not_certifiable")
                    item = s.scalar(select(InventoryItemModel).where(
                        InventoryItemModel.tenant_id == tenant_id,
                        InventoryItemModel.id == item_id, InventoryItemModel.active.is_(True),
                    ))
                    location = s.scalar(select(InventoryLocationModel).where(
                        InventoryLocationModel.tenant_id == tenant_id,
                        InventoryLocationModel.id == location_id, InventoryLocationModel.active.is_(True),
                    ))
                    if (item is None or location is None
                            or _normal(item.name) != _normal(row["material"])
                            or _normal(location.name) != _normal(sheet)
                            or not _unit_match(item, row["form"], row["unit"])):
                        raise CatinatCertificationBlocked("catalog_changed_after_preview")
                    prior = s.scalar(select(InventoryCatinatCatalogCertificationModel).where(
                        InventoryCatinatCatalogCertificationModel.tenant_id == tenant_id,
                        InventoryCatinatCatalogCertificationModel.spreadsheet_file_id == spreadsheet,
                        InventoryCatinatCatalogCertificationModel.sheet_name == sheet,
                        InventoryCatinatCatalogCertificationModel.cell_ref == cell,
                    ).order_by(InventoryCatinatCatalogCertificationModel.version.desc()).limit(1))
                    record = InventoryCatinatCatalogCertificationModel(
                        tenant_id=tenant_id, approval_request_id=request_id,
                        version=(prior.version + 1) if prior else 1,
                        business_date=business_date, spreadsheet_file_id=spreadsheet,
                        sheet_name=sheet, cell_ref=cell, source_hash=digest,
                        source_proposal_hash=approval.proposal_hash,
                        material_display=row["material"], form=row["form"], unit=row["unit"],
                        item_id=item_id, location_id=location_id,
                        approved_by=approval.decided_by, certified_by=actor_id,
                        certification_note=note.strip(), certified_at=inventory_utcnow(),
                        status="evidence_only",
                    )
                    s.add(record)
                    s.flush()
                    return _public(record)
            except IntegrityError as exc:
                raise CatinatCertificationBlocked("certification_concurrency_conflict") from exc

    def list(self, *, tenant_id: str, business_date: date, limit: int = 100) -> list[dict]:
        with self.sessions() as s:
            rows = s.scalars(select(InventoryCatinatCatalogCertificationModel).where(
                InventoryCatinatCatalogCertificationModel.tenant_id == tenant_id,
                InventoryCatinatCatalogCertificationModel.business_date == business_date,
            ).order_by(
                InventoryCatinatCatalogCertificationModel.version.desc(),
                InventoryCatinatCatalogCertificationModel.certified_at.desc(),
            ).limit(min(max(limit, 1), 100))).all()
            return [_public(record) for record in rows]
