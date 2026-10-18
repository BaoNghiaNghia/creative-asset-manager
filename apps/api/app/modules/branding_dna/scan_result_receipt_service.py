"""Internal-only, transactionally durable *quarantined* result ACK primitive.

NO HTTP route imports this service. /lease never issues jobs. It must stay
unreachable until a separate externally verified provider grant, signed
envelope, and rollout switch exist. A receipt records storage, not provenance.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.branding_dna.model import BrandShopModel, BrandWorkerModel, now_utc
from app.modules.branding_dna.task_model import BrandScanTaskModel
from app.modules.branding_dna.task_service import _fenced_task
from app.modules.branding_dna.evidence_model import BrandListingSnapshotModel
from app.modules.branding_dna.scan_receipt_model import BrandScanFailureReceiptModel
from app.modules.branding_dna.scan_result_receipt_model import BrandScanResultReceiptModel
from app.modules.branding_dna.scan_result_contract import (
    ResultManifest, manifest_digest, valid_capture_window,
)

MAX_BYTES = 8192


class FutureResultPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_receipt_id: UUID
    client_payload_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    task_id: UUID
    lease_id: UUID
    lease_generation: int = Field(strict=True, ge=1, le=1_000_000)
    shop_id: UUID
    snapshot_id: UUID
    manifest: ResultManifest


def _uuid(value: UUID) -> str:
    return str(value).lower()


def _manifest_data(manifest: ResultManifest) -> dict:
    # Exact insertion order of the dormant Node worker's JSON.stringify DTO.
    stamp = manifest.captured_at.astimezone(timezone.utc)
    time_ms = stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "listing_key": manifest.listing_key,
        "observed_revision": manifest.observed_revision,
        "captured_at": time_ms,
        "page_count": manifest.page_count,
        "images": [{
            "sequence_index": image.sequence_index,
            "asset_sha256": image.asset_sha256,
            "source_url_sha256": image.source_url_sha256,
            "role": image.role,
            "rights_state": image.rights_state,
        } for image in manifest.images],
    }


def _canonical_packet(body: FutureResultPacket) -> tuple[dict, str, str]:
    manifest = _manifest_data(body.manifest)
    canonical = {
        "task_id": _uuid(body.task_id),
        "lease_id": _uuid(body.lease_id),
        "lease_generation": body.lease_generation,
        "shop_id": _uuid(body.shop_id),
        "snapshot_id": _uuid(body.snapshot_id),
        "manifest": manifest,
    }
    raw = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise HTTPException(422, detail="result_manifest_too_large")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if body.client_payload_sha256 != digest:
        raise HTTPException(409, detail="Result payload integrity mismatch")
    return canonical, json.dumps(manifest, ensure_ascii=False,
                                 separators=(",", ":")), digest


def _ack(receipt: BrandScanResultReceiptModel, *, replayed: bool) -> dict:
    # Exact ACK schema expected by the unconnected Node outbox.
    return {
        "accepted": True,
        "client_receipt_id": receipt.client_receipt_id,
        "client_payload_sha256": receipt.client_payload_sha256,
        "task_id": receipt.task_id,
        "lease_id": receipt.lease_id,
        "lease_generation": receipt.lease_generation,
        "shop_id": receipt.shop_id,
        "snapshot_id": receipt.snapshot_id,
        "status": "quarantined_unverified",
        "receipt_id": receipt.id,
        "replayed": replayed, "persisted": True, "receipt_issued": True,
        "provenance_verified": False,
        "marketplace_authorization_verified": False,
        "scan_enabled": False,
    }


def persist_quarantined_future_result(
    session: Session, *,
    tenant_id: str, agent_id: str, body: FutureResultPacket,
) -> dict:
    """For integration tests/internal future use; NEVER a public endpoint.

    Serializes by task row lock, fences lease, writes immutable quarantine
    receipt and blocks task for human review in ONE transaction.
    Replay of an identical result requires an enabled worker but does not
    require the lease to remain live; changed payload/identity returns 409.
    """
    task_id = _uuid(body.task_id)
    lease_id = _uuid(body.lease_id)
    shop_id = _uuid(body.shop_id)
    snapshot_id = _uuid(body.snapshot_id)
    _, manifest_json, digest = _canonical_packet(body)

    task = session.scalar(select(BrandScanTaskModel).where(
        BrandScanTaskModel.tenant_id == tenant_id,
        BrandScanTaskModel.id == task_id,
    ).with_for_update())
    if task is None or task.shop_id != shop_id or task.snapshot_id != snapshot_id:
        raise HTTPException(409, detail="Result task shop or snapshot mismatch")
    worker_enabled = session.scalar(select(BrandWorkerModel.id).where(
        BrandWorkerModel.id == agent_id,
        BrandWorkerModel.tenant_id == tenant_id,
        BrandWorkerModel.enabled.is_(True),
    ))
    if worker_enabled is None:
        raise HTTPException(409, detail="Worker authorization revoked")
    prior = session.scalar(select(BrandScanResultReceiptModel).where(
        BrandScanResultReceiptModel.tenant_id == tenant_id,
        BrandScanResultReceiptModel.task_id == task_id,
        BrandScanResultReceiptModel.lease_generation == body.lease_generation,
    ))
    if prior is not None:
        if (prior.client_receipt_id != _uuid(body.client_receipt_id)
                or prior.agent_id != agent_id
                or prior.lease_id != lease_id
                or prior.client_payload_sha256 != digest
                or prior.snapshot_id != snapshot_id
                or prior.shop_id != shop_id
                or prior.manifest_json != manifest_json):
            raise HTTPException(409, detail="Conflicting immutable result receipt")
        return _ack(prior, replayed=True)

    # A terminal failure receipt for this lease generation wins, never both.
    fail = session.scalar(select(BrandScanFailureReceiptModel).where(
        BrandScanFailureReceiptModel.tenant_id == tenant_id,
        BrandScanFailureReceiptModel.task_id == task_id,
        BrandScanFailureReceiptModel.lease_generation == body.lease_generation,
    ))
    if fail is not None:
        raise HTTPException(409, detail="Terminal failure already acknowledged")

    now = now_utc()
    _fenced_task(
        session, tenant_id=tenant_id, task_id=task_id,
        agent_id=agent_id, lease_id=lease_id,
        lease_generation=body.lease_generation, now=now,
    )
    shop = session.scalar(select(BrandShopModel).where(
        BrandShopModel.tenant_id == tenant_id, BrandShopModel.id == shop_id,
        BrandShopModel.access_mode.in_(("owned", "authorized")),
    ))
    snapshot = session.scalar(select(BrandListingSnapshotModel).where(
        BrandListingSnapshotModel.tenant_id == tenant_id,
        BrandListingSnapshotModel.shop_id == shop_id,
        BrandListingSnapshotModel.id == snapshot_id,
    ))
    if (shop is None or snapshot is None
            or task.task_kind != "verify_listing_evidence"
            or snapshot.listing_key != body.manifest.listing_key
            or not valid_capture_window(body.manifest.captured_at, now=now)):
        raise HTTPException(409, detail="Result source identity invalid or expired")
    m_hash = manifest_digest(
        tenant_id=tenant_id, shop_id=shop_id, task_id=task_id,
        snapshot_id=snapshot_id, snapshot_sha256=snapshot.content_sha256,
        manifest=body.manifest,
    )
    receipt = BrandScanResultReceiptModel(
        tenant_id=tenant_id, task_id=task_id, shop_id=shop_id,
        snapshot_id=snapshot_id, snapshot_sha256=snapshot.content_sha256,
        agent_id=agent_id, lease_id=lease_id,
        lease_generation=body.lease_generation,
        client_receipt_id=_uuid(body.client_receipt_id),
        client_payload_sha256=digest, manifest_sha256=m_hash,
        manifest_json=manifest_json,
        gallery_count=len(body.manifest.images),
        page_count=body.manifest.page_count,
        status="quarantined_unverified",
    )
    session.add(receipt)
    # No automatic evidence verification/completion; block instead of retrying
    # a quarantined result while awaiting separate human provenance review.
    task.status = "blocked"
    task.last_error_code = "manual_check_required"
    task.lease_id = None
    task.agent_id = None
    task.lease_until = None
    task.retry_after = None
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="Concurrent result receipt: retry") from exc
    session.refresh(receipt)
    return _ack(receipt, replayed=False)
