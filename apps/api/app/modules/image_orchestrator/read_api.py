"""P2D-0: authenticated discovery only, no database migration or execution.
The central store is absent. Unknown counts must not be interpreted as zero.
Do not admit, claim, render, upload or reconcile a pack in this release.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, Response
from . import policy as p
from .identity import ImageIdentity, image_identity

REGISTRY = Path(__file__).with_name("skill_registry_candidate.json")
BLOCKERS = (
    "CENTRAL_SCHEMA_NOT_MIGRATED",
    "FULL_PRODUCTION_MIGRATION_AND_RESTORE_NOT_VERIFIED",
    "TRUSTED_ARTWORK_AND_QA_REVIEWER_NOT_CONNECTED",
    "LIVE_RESOURCE_INTEGRATION_NOT_VERIFIED",
    "LIVE_DRIVE_AND_RECONCILIATION_NOT_VERIFIED",
    "IMAGE_CLAIM_API_NOT_ENABLED",
    "LEGACY_EXTENSION_CUTOVER_NOT_VERIFIED",
)


def is_read_api_enabled() -> bool:
    # P2D-0: read-only registry before additive schema upgrade.
    # Explicit false is a read-only rollback switch; never grants execution.
    return os.environ.get("CAM_IMAGE_READ_API_ENABLED", "true").lower() in ("1", "true")


def read_api_enabled() -> None:
    if not is_read_api_enabled():
        raise HTTPException(404, "image_api_not_enabled")


router = APIRouter(
    prefix="/api/v1/muse-bridge", tags=["image-orchestrator"],
    dependencies=[Depends(read_api_enabled)],
)
Identity = Annotated[ImageIdentity, Depends(image_identity)]
ListingId = Annotated[str, ApiPath(pattern=r"^[0-9]{5,20}$")]
PackId = Annotated[str, Query(pattern=r"^[a-f0-9]{64}$")]
Sku = Annotated[str, Query(pattern=r"^(C1717|G500|G180|G185)$")]


def _no_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _registry() -> dict:
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        valid = (
            data["protocol"] == p.PROTOCOL
            and data["skillName"] == "print-variant-model-10-image"
            and data["skillVersion"] == p.SKILL_VERSION
            and data["skillSha256"] == p.SKILL_SHA256
            and data["roleContract"] == p.ROLE_CONTRACT
            and data["placementPolicy"] == p.PLACEMENT_CONTRACT
            and data["qaPolicy"] == p.QA_POLICY
            and data["neckTagRole"] == 4
            and data["nativePilotMinimumPx"] == p.PILOT_MIN_NATIVE
            and data["publicationMinimumPx"] == p.PUBLICATION_MIN_NATIVE
            and data["claimEnabled"] is False
            and isinstance(data["files"], dict)
            and len(data["files"]) == 19
            and all(isinstance(k, str) and k.startswith("print-variant-model-10-image/")
                    and ".." not in k.split("/") and p.valid_sha(v)
                    for k, v in data["files"].items())
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(503, "image_registry_invalid")
    return data


def _gate(identity: ImageIdentity) -> dict:
    return {
        "protocol": p.PROTOCOL,
        "jobType": "image",
        "workerId": identity.worker,
        "workerKind": identity.kind,
        "claimEnabled": False,
        "imageClaimAllowed": False,
        "liveClaimEnabled": False,
        "rendererCalled": False,
        "rendererAllowed": False,
        "centralSchemaReady": False,
        "releasePhase": "P2D_DISCOVERY_ONLY",
        "status": "PREPARED_DISABLED",
        "blockers": list(BLOCKERS),
    }


@router.get("/skill-registry")
def skill_registry(response: Response, identity: Identity):
    _no_cache(response)
    return {**_registry(), **_gate(identity),
            "registryStatus": "candidate",
            "paletteVerified": False,
            "printBoxAuthority": "CAM_PRINT_BOX_SINGLE_AUTHORITY_V1",
            "waveBarrier": "global_wave1_before_wave2_per_batch",
            "holdIsolationSeconds": p.BARRIER_SECONDS,
            "leaseSeconds": p.LEASE_SECONDS, "heartbeatSeconds": p.HEARTBEAT_SECONDS}


@router.get("/image-commands")
def image_commands(response: Response, identity: Identity,
                   limit: Annotated[int, Query(ge=1, le=20)] = 20):
    _no_cache(response)
    return {**_gate(identity), "items": [], "commands": [],
            "queueAuthoritative": False, "reason": "IMAGE_CLAIMS_DISABLED",
            "limit": limit}


@router.get("/image-work")
def image_work(response: Response, identity: Identity,
               limit: Annotated[int, Query(ge=1, le=20)] = 20):
    _no_cache(response)
    return {**_gate(identity), "flowRunningRequested": None,
            "flowRunning": False, "items": [], "limit": limit,
            "centralPackCount": None, "centralRoleCounts": None,
            "remainingWithoutImages": None, "queueAuthoritative": False,
            "counterScope": "central_schema_not_migrated",
            "reason": "IMAGE_CLAIMS_DISABLED"}


@router.get("/jobs/{listing_id}/image-status")
@router.get("/jobs/{listing_id}/image-contract")
def image_pack_unavailable(response: Response, identity: Identity,
                           listing_id: ListingId, packId: PackId, sku: Sku):
    # Without a central schema the existence and ownership of a pack are unknown.
    _no_cache(response)
    raise HTTPException(503, "image_central_schema_not_migrated",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/jobs/{listing_id}/images/{role}/qa-status")
def image_qa_unavailable(response: Response, identity: Identity,
                         listing_id: ListingId, packId: PackId, sku: Sku,
                         role: Annotated[int, ApiPath(ge=1,le=13)],
                         attemptId: Annotated[str,Query(pattern=r"^[A-Za-z0-9_-]{1,128}$")],
                         imageSha256: Annotated[str,Query(pattern=r"^[a-f0-9]{64}$")]):
    _no_cache(response)
    raise HTTPException(503, "image_central_schema_not_migrated",
                        headers={"Cache-Control": "private, no-store"})
