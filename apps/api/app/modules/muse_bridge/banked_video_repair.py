"""One operator-authorized native MP4 replacement, without a render or job lease.

The old receipt and Drive object remain valid until the replacement has been
verified. A revision-bound storage identity makes retrying the same bytes safe.
No change to the worker routing, provider budget, retry or claim policy.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .model import CamMuseCommand, CamMuseJob
from .story_lite import LITE_DURATION_BOUNDS, LITE_OUTPUT, LITE_POLICY, LITE_SOURCE
from .video_storage import store_muse_video, validate_mp4

POLICY = "CAM_BANKED_VIDEO_REPLACEMENT_V1"
MARKER = "bankedVideoRepairByAspect"
ROOT = "/api/v1/muse-bridge/banked-video-repairs"
SLUGS = {"9:16": "vertical", "16:9": "horizontal"}
RATIOS = {v: k for k, v in SLUGS.items()}
SHA_PATTERN = r"^[0-9a-f]{64}$"


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expectedVideoSha256: str = Field(pattern=SHA_PATTERN)
    expectedArtworkSha256: str = Field(pattern=SHA_PATTERN)
    expectedReplacementSha256: str | None = Field(default=None, pattern=SHA_PATTERN)
    workerId: Literal["muse-01", "muse-02"]
    reasonCode: Literal["HEAD_PROPORTION_DRIFT", "SCENE_REALISM", "PRINT_SIZE"]


def config():
    return {
        "version": POLICY,
        "scope": "one_operator_authorized_existing_native_mp4_only",
        "statusPathTemplate": ROOT + "/{listing_id}/{aspect}",
        "uploadPathTemplate": ROOT + "/{listing_id}/{aspect}/{request_id}/upload",
        "requiresOperatorApproval": True,
        "requiresDesignatedWorker": True,
        "requiredHeaders": ["X-CAM-Video-Sha256", "X-CAM-Artwork-Sha256",
                            "X-CAM-Banked-QA", "X-CAM-Story-Output-Contract",
                            "X-CAM-Motion-Version"],
        "allowedDuringRendererCooldown": "transfer_existing_bytes_only",
        "newVideoClaimAllowed": False,
        "renderAllowed": False,
        "preserveOldVideoUntilVerified": True,
        "preserveSibling": True,
        "instructions": (
            "For an explicitly authorized banked repair, retrieve its listing/aspect "
            "request using the designated worker credential. Only upload the exact "
            "existing corrected native MP4, after verifying the artwork and worker "
            "visual QA. Never generate a new take for this storage-only request. "
            "If the local file is unavailable, report it and leave the old receipt intact. "
            "Do not change any cooldown or ordinary claim policy."
        ),
    }


def _marker(receipts, ratio):
    value = (receipts.get(MARKER) or {}).get(ratio)
    return value if isinstance(value, dict) and value.get("policyVersion") == POLICY else None


def pending_request(receipts, ratio, now):
    marker = _marker(receipts, ratio)
    if not marker or marker.get("state") != "pending_upload":
        return None
    try:
        until = datetime.fromisoformat(marker["expiresAtUtc"])
        return marker if until.tzinfo is not None and until > now else None
    except (ValueError, KeyError, TypeError):
        return None


def _load(db, tenant, listing_id, *, locked=False):
    from . import router as muse
    if not muse.LISTING_ID.fullmatch(listing_id):
        raise HTTPException(404, "Listing not found")
    query = select(CamMuseJob).where(CamMuseJob.tenant_id == tenant,
                                     CamMuseJob.listing_key == listing_id)
    if locked:
        query = query.with_for_update().execution_options(populate_existing=True)
    job = db.scalar(query)
    if job is None:
        raise HTTPException(404, "Listing not found")
    return job


def _assert_current(db, tenant, job, ratio, old_sha, artwork_sha):
    from . import router as muse
    from . import regeneration
    rec = job.output_receipts or {}
    uploads = rec.get("drive_uploads") or {}
    if (job.status != "submitted" or job.lease_hash or job.lease_expires_at
            or job.source_state != "ready_for_muse"
            or job.artwork_sha256 != artwork_sha
            or not muse.latest_artwork.current(db, tenant, job)
            or not muse._story_lite_pin(muse._pinned_story_v33(job))
            or rec.get("videoContentQa") != "pending_review"
            or (muse._qa_record(job) or {}).get("verdict") != "pass"
            or (muse._qa_record(job) or {}).get("artwork_sha256") != artwork_sha
            or muse._verified_video_ratios(job) != set(SLUGS)
            or uploads.get(ratio, {}).get("sha256") != old_sha
            or any(v.get("storyOutputContract") != LITE_OUTPUT for v in uploads.values())
            or len({v.get("drive_file_id") for v in uploads.values()}) != 2
            or rec.get("videoCapabilityHold") or muse._automation_soft_hold_active(rec)
            or muse.skips.active(job) or regeneration.requested_aspects(job)):
        raise HTTPException(409, "Banked replacement requires the current idle verified Story Lite video")
    active = db.scalar(select(CamMuseCommand.command_id).where(
        CamMuseCommand.tenant_id == tenant, CamMuseCommand.listing_key == job.listing_key,
        CamMuseCommand.status.in_(("pending", "claimed"))).limit(1))
    if active:
        raise HTTPException(409, "Listing has existing command work; no replacement")
    return rec, uploads


def _response(listing, aspect, marker):
    return {"ok": True, **marker, "listingId": listing, "aspect": aspect,
            "uploadPath": f"{ROOT}/{listing}/{aspect}/{marker['requestId']}/upload",
            "renderAllowed": False, "newVideoClaimAllowed": False}


def stage(db: Session, tenant: str, listing_id: str, aspect: str, payload: StageRequest):
    from . import router as muse
    if aspect not in RATIOS:
        raise HTTPException(422, "Invalid aspect")
    ratio = RATIOS[aspect]
    now = muse._utc()
    job = _load(db, tenant, listing_id, locked=True)
    rec = job.output_receipts or {}
    prior = _marker(rec, ratio)
    fields = payload.model_dump()
    if prior and all(prior.get(k) == v for k, v in fields.items()):
        if pending_request(rec, ratio, now):
            _assert_current(db, tenant, job, ratio, payload.expectedVideoSha256,
                            payload.expectedArtworkSha256)
            return _response(listing_id, aspect, prior)
        if (prior.get("state") == "uploaded"
                and (rec.get("drive_uploads") or {}).get(ratio, {}).get("sha256")
                == prior.get("replacementSha256")):
            return _response(listing_id, aspect, prior)
    if pending_request(rec, ratio, now):
        raise HTTPException(409, "An existing banked replacement request is pending")
    rec, uploads = _assert_current(db, tenant, job, ratio, payload.expectedVideoSha256,
                                   payload.expectedArtworkSha256)
    if payload.expectedReplacementSha256 and payload.expectedReplacementSha256 in {
            v.get("sha256") for v in uploads.values()}:
        raise HTTPException(422, "Replacement must differ from both current videos")
    marker = {**fields, "policyVersion": POLICY, "requestId": uuid4().hex,
              "aspectRatio": ratio, "state": "pending_upload",
              "requestedAtUtc": now.isoformat(),
              "expiresAtUtc": (now + timedelta(hours=48)).isoformat(),
              "replacementSha256": payload.expectedReplacementSha256,
              "oldVideoRetained": True, "workerQaIsIndependentQa": False}
    by_aspect = dict(rec.get(MARKER) or {})
    by_aspect[ratio] = marker
    job.output_receipts = {**rec, MARKER: by_aspect}
    job.updated_at = now
    db.commit()
    return _response(listing_id, aspect, marker)


def read(db: Session, tenant: str, listing_id: str, aspect: str, worker: str,
         *, request_id: str | None = None, locked: bool = False):
    from . import router as muse
    if aspect not in RATIOS:
        raise HTTPException(404, "Banked replacement not found")
    ratio = RATIOS[aspect]
    job = _load(db, tenant, listing_id, locked=locked)
    rec = job.output_receipts or {}
    marker = _marker(rec, ratio)
    if (not marker or marker.get("workerId") != worker
            or (request_id is not None and marker.get("requestId") != request_id)):
        raise HTTPException(404, "Banked replacement not found for this worker")
    if marker.get("state") == "uploaded":
        if ((rec.get("drive_uploads") or {}).get(ratio, {}).get("sha256") != marker.get("replacementSha256")
                or job.artwork_sha256 != marker.get("expectedArtworkSha256")):
            raise HTTPException(409, "Banked replacement no longer current")
    else:
        if not pending_request(rec, ratio, muse._utc()):
            raise HTTPException(410, "Banked replacement expired")
        _assert_current(db, tenant, job, ratio, marker["expectedVideoSha256"],
                        marker["expectedArtworkSha256"])
    return job, marker


def status(db, tenant, listing_id, aspect, worker):
    _, marker = read(db, tenant, listing_id, aspect, worker)
    return _response(listing_id, aspect, marker)


async def upload(db: Session, tenant: str, listing_id: str, aspect: str,
                 request_id: str, worker: str, *, mp4: bytes, video_sha: str,
                 artwork_sha: str, qa_attestation: str, output_contract: str,
                 motion_version: str):
    from . import router as muse
    from .garment_policy import GARMENT_SKU, GARMENT_POLICY_VERSION

    if (qa_attestation != "worker_visual_pass" or output_contract != LITE_OUTPUT
            or motion_version != muse.STORY_VERSION
            or not muse.HEX64.fullmatch(video_sha or "")
            or not muse.HEX64.fullmatch(artwork_sha or "")
            or hashlib.sha256(mp4).hexdigest() != video_sha):
        raise HTTPException(422, "Exact banked MP4 SHA and worker visual QA attestation required")
    metadata = validate_mp4(mp4, aspect)
    duration = metadata.get("durationSeconds")
    if (duration is None or not LITE_DURATION_BOUNDS[0] <= duration <= LITE_DURATION_BOUNDS[1]
            or not metadata.get("width") or not metadata.get("height")):
        raise HTTPException(422, "Banked MP4 must match the pinned native duration and aspect")
    job, marker = read(db, tenant, listing_id, aspect, worker,
                       request_id=request_id, locked=True)
    ratio = RATIOS[aspect]
    if artwork_sha != marker["expectedArtworkSha256"]:
        raise HTTPException(409, "Artwork SHA differs from operator authorization")
    if marker.get("replacementSha256") not in (None, video_sha):
        raise HTTPException(409, "Replacement request is already bound to different bytes")
    if marker["state"] == "uploaded":
        return {**_response(listing_id, aspect, marker), "reused": True,
                "storageVerified": True, "videoContentQa": "pending_review"}
    rec, uploads = _assert_current(db, tenant, job, ratio, marker["expectedVideoSha256"], artwork_sha)
    if video_sha in {v.get("sha256") for v in uploads.values()}:
        raise HTTPException(422, "Banked MP4 is an already saved video")
    # Bind once before network I/O. A retry can only reuse the same bytes, even
    # if Drive stored the file and the previous HTTP response was lost.
    marker = {**marker, "replacementSha256": video_sha}
    by_aspect = dict(rec[MARKER])
    by_aspect[ratio] = marker
    job.output_receipts = {**rec, MARKER: by_aspect}
    db.commit()
    stored = await store_muse_video(tenant_id=tenant, listing_id=listing_id,
        artwork_sha256=artwork_sha, aspect=aspect, mp4=mp4,
        required_duration_seconds=LITE_DURATION_BOUNDS, storage_revision=request_id)
    if (stored.get("sha256") != video_sha or stored.get("storageRevision") != request_id
            or stored.get("storageVerified") is not True
            or stored.get("nativeBytesPreserved") is not True
            or stored.get("aspectRatio") != ratio
            or stored.get("storage_provider") != "google_drive_managed"):
        raise HTTPException(502, "Replacement storage proof invalid")
    db.expire_all()
    job, marker = read(db, tenant, listing_id, aspect, worker, request_id=request_id, locked=True)
    if marker["state"] == "uploaded":
        return {**_response(listing_id, aspect, marker), "reused": True,
                "storageVerified": True, "videoContentQa": "pending_review"}
    if marker.get("replacementSha256") != video_sha:
        raise HTTPException(409, "Replacement authorization changed during upload")
    rec, uploads = _assert_current(db, tenant, job, ratio, marker["expectedVideoSha256"], artwork_sha)
    rejected = uploads[ratio]
    if (not stored.get("drive_file_id") or not stored.get("drive_folder_id")
            or stored["drive_file_id"] in {v.get("drive_file_id") for v in uploads.values()}
            or stored["drive_folder_id"] != rejected.get("drive_folder_id")
            or stored.get("relativeFolder") != rejected.get("relativeFolder")):
        raise HTTPException(502, "Replacement did not create an isolated verified Drive object")
    now = muse._utc()
    stored = {**stored, "storyVersion": muse.STORY_VERSION, "storyOutputContract": LITE_OUTPUT,
              "storySourceContract": LITE_SOURCE, "creativePolicy": LITE_POLICY,
              "storyQaStatus": "pending_review", "characterQaStatus": "pending_review",
              "sourcePlacementQa": "not_required", "sourceVisualContentIndependentlyVerified": False,
              "garmentSkuContract": GARMENT_SKU, "garmentPolicyVersion": GARMENT_POLICY_VERSION,
              "garmentQaStatus": "pending_independent_visual_review",
              "motionPromptVersion": motion_version, "motionVersionSource": "muse_declared",
              "bankedReplacementRequestId": request_id}
    history = dict(rec.get("rejectedVideoHistoryByAspect") or {})
    history[ratio] = list(history.get(ratio) or []) + [{
        "videoContentQa": "fail", "reasonCode": marker["reasonCode"],
        "reason": "Operator-authorized replacement of worker-reported realism/print defect.",
        "reviewSource": "worker_reported_operator_authorized", "reviewedAtUtc": now.isoformat(),
        "artworkSha256": artwork_sha, "videoSha256": rejected["sha256"],
        "receipt": rejected, "archiveMode": "drive_original_retained",
        "replacementRequestId": request_id}]
    marker = {**marker, "state": "uploaded", "uploadedAtUtc": now.isoformat(),
              "storageVerified": True, "workerQaAttestation": qa_attestation}
    uploads = {**uploads, ratio: stored}
    by_aspect = {**rec[MARKER], ratio: marker}
    revisions = {**(rec.get("videoStorageRevisionByAspect") or {}), ratio: request_id}
    new = {**rec, MARKER: by_aspect, "videoStorageRevisionByAspect": revisions,
           "rejectedVideoHistoryByAspect": history, "drive_uploads": uploads,
           "videos": [uploads["9:16"], uploads["16:9"]],
           "videoContentQa": "pending_review", "videoStoryQa": "pending_review",
           "videoCharacterQa": "pending_review", "videoPrintQa": "pending_review",
           "videoGarmentQa": "pending_independent_visual_review"}
    # A previous hero is not evidence for the corrected take. Preserve only
    # the sibling's sources; keep all old receipt metadata in repair history.
    for key in ("hero_sources", "hero_revisions", "story_action_preflight"):
        if isinstance(new.get(key), dict):
            new[key] = {r: v for r, v in new[key].items() if r != ratio}
    job.output_receipts = new
    job.updated_at = now
    # No render-success timestamp or attempt/lease mutation: this is a transfer.
    db.commit()
    return {**_response(listing_id, aspect, marker), "reused": False,
            "storageVerified": True, "videoContentQa": "pending_review"}
