"""Phase 3C.3B2C2A: human-requested, independently retained Stage 1 PNG.

Feature-gated, no model call or reviewer verdict. A durable reservation blocks
duplicate uploads after crashes; a stuck reservation needs an audited manual
reconciliation (never silently re-upload). Output History may later be deleted.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.providers.contracts import StoreAssetInput
from app.modules.auth_persistence.model import AuthAuditEventModel

from .model import AgentTrialArtworkSnapshotModel
from .trial_artwork_lineage import (
    ArtworkLineageClaim, TrialArtworkLineageError, artwork_lineage,
)
from .trial_artwork_bytes import read_verified_artwork_png


class TrialArtworkSnapshotError(ValueError):
    """Fixed sanitized code: no prompt, keyword, provider identity or secret."""


def _asset_id(tenant_id: str, output_version_id: str) -> str:
    tenant_hash = sha256(tenant_id.encode("utf-8")).hexdigest()[:20]
    return f"agent-trial-qa-snapshot:{tenant_hash}:{output_version_id}"


def snapshot_status(row: AgentTrialArtworkSnapshotModel | None, *, created: bool = False) -> dict:
    if row is None:
        return {
            "snapshot_status": "not_captured",
            "independent_qa_verified": False,
            "eligible_for_a_b_evaluation": False,
            "automatic_promotion_enabled": False,
        }
    return {
        "snapshot_id": row.snapshot_id,
        "source_output_version_id": row.source_output_version_id,
        "snapshot_status": row.status,
        "artifact_sha256": row.artifact_sha256,
        "size_bytes": row.size_bytes,
        "width": row.width,
        "height": row.height,
        "stored_copy_verified": row.status == "verified",
        "created": created,
        "independent_qa_verified": False,
        "eligible_for_a_b_evaluation": False,
        "automatic_promotion_enabled": False,
    }


def get_snapshot(
    session: Session, *, tenant_id: str, study_id: str, output_version_id: str,
) -> AgentTrialArtworkSnapshotModel | None:
    return session.scalar(select(AgentTrialArtworkSnapshotModel).where(
        AgentTrialArtworkSnapshotModel.tenant_id == tenant_id,
        AgentTrialArtworkSnapshotModel.study_id == study_id,
        AgentTrialArtworkSnapshotModel.source_output_version_id == output_version_id,
    ))


def _matches(row: AgentTrialArtworkSnapshotModel, claim: ArtworkLineageClaim) -> bool:
    return (
        row.keyword_id == claim.keyword_id
        and row.image_processing_job_id == claim.image_processing_job_id
        and row.brief_processing_job_id == claim.brief_processing_job_id
        and row.source_remote_file_id == claim.remote_file_id
        and row.artifact_sha256 == claim.sha256
        and row.size_bytes == claim.size_bytes
        and row.width == claim.width
        and row.height == claim.height
        and row.snapshot_asset_id == _asset_id(claim.tenant_id, claim.output_version_id)
    )


async def capture_snapshot(
    session: Session, storage: Any, *,
    claim: ArtworkLineageClaim, actor_id: str,
) -> dict:
    """Reserve -> independently verify source -> upload separate copy ->
    independently verify copy -> seal. No automatic retry after uncertainty.
    """
    existing = get_snapshot(
        session, tenant_id=claim.tenant_id, study_id=claim.study_id,
        output_version_id=claim.output_version_id,
    )
    if existing is not None:
        if not _matches(existing, claim):
            raise TrialArtworkSnapshotError("trial_snapshot_lineage_conflict")
        if existing.status != "verified":
            raise TrialArtworkSnapshotError("trial_snapshot_requires_manual_recovery")
        return snapshot_status(existing)

    row = AgentTrialArtworkSnapshotModel(
        tenant_id=claim.tenant_id, snapshot_id=str(uuid4()),
        study_id=claim.study_id, keyword_id=claim.keyword_id,
        source_output_version_id=claim.output_version_id,
        image_processing_job_id=claim.image_processing_job_id,
        brief_processing_job_id=claim.brief_processing_job_id,
        source_remote_file_id=claim.remote_file_id,
        snapshot_asset_id=_asset_id(claim.tenant_id, claim.output_version_id),
        snapshot_remote_file_id=None, artifact_sha256=claim.sha256,
        size_bytes=claim.size_bytes, width=claim.width, height=claim.height,
        status="reserved", created_by=actor_id,
    )
    session.add(row)
    try:
        # Durable reservation *before* Google Drive I/O. If a crash occurs
        # after upload, a replay must NOT claim to have verified the copy.
        session.commit()
    except IntegrityError:
        session.rollback()
        raise TrialArtworkSnapshotError("trial_snapshot_concurrent_reservation") from None

    status, body = await read_verified_artwork_png(storage, claim)
    if not status["storage_bytes_verified"]:
        raise TrialArtworkSnapshotError("trial_snapshot_source_unverified")

    # Never upload a stale source after an expensive remote verification.
    session.expire_all()
    if artwork_lineage(
        session, tenant_id=claim.tenant_id, study_id=claim.study_id,
        output_version_id=claim.output_version_id,
    ) != claim:
        raise TrialArtworkLineageError("trial_snapshot_source_changed")

    async def stream():
        yield body

    stored = await storage.store_asset(StoreAssetInput(
        tenant_id=claim.tenant_id,
        asset_id=row.snapshot_asset_id,
        content_hash=claim.sha256,
        body=stream(),
        content_type="image/png", size_bytes=claim.size_bytes,
        filename="trial-qa-" + claim.output_version_id + ".png",
    ))
    if (
        not isinstance(stored.remote_file_id, str)
        or not 1 <= len(stored.remote_file_id) <= 255
        or stored.remote_file_id == claim.remote_file_id
        or stored.content_hash != claim.sha256
        or (stored.size_bytes is not None and stored.size_bytes != claim.size_bytes)
    ):
        raise TrialArtworkSnapshotError("trial_snapshot_storage_identity_invalid")

    # The new copy is independent of the mutable Output History file; verify
    # that Drive reads back the exact same PNG before sealing its registry row.
    copied_claim = replace(claim, remote_file_id=stored.remote_file_id)
    result, _ = await read_verified_artwork_png(storage, copied_claim)
    if not result["storage_bytes_verified"]:
        raise TrialArtworkSnapshotError("trial_snapshot_copy_unverified")

    session.expire_all()
    if artwork_lineage(
        session, tenant_id=claim.tenant_id, study_id=claim.study_id,
        output_version_id=claim.output_version_id,
    ) != claim:
        raise TrialArtworkLineageError("trial_snapshot_source_changed")
    reserved = session.scalar(select(AgentTrialArtworkSnapshotModel).where(
        AgentTrialArtworkSnapshotModel.tenant_id == claim.tenant_id,
        AgentTrialArtworkSnapshotModel.snapshot_id == row.snapshot_id,
    ).with_for_update())
    if reserved is None or reserved.status != "reserved" or not _matches(reserved, claim):
        raise TrialArtworkSnapshotError("trial_snapshot_reservation_changed")
    reserved.snapshot_remote_file_id = stored.remote_file_id
    reserved.verified_at = datetime.now(timezone.utc)
    reserved.status = "verified"
    session.add(AuthAuditEventModel(
        tenant_id=claim.tenant_id, actor_id=actor_id,
        action="agent_trial_artwork_snapshot_verified",
        detail_json={
            "study_id": claim.study_id,
            "snapshot_id": row.snapshot_id,
            "source_output_version_id": claim.output_version_id,
            "storage_copy_verified": True,
        },
    ))
    session.commit()
    return snapshot_status(reserved, created=True)
