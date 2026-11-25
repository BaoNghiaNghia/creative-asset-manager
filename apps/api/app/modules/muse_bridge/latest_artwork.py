"""Use CAM's current saved Native Master generation, never an agent's stale cache.

Revision switches are performed by the existing bounded recovery sweep. Active
leases remain pinned. Old Drive objects and receipt/QA/attempt history are
retained; a different original SHA requires genuine new artwork QA.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import os
import re

from fastapi import HTTPException
from sqlalchemy import select
from app.modules.processing.model import ProcessingJobModel as P
from app.modules.etsy_spy.extraction import EXTRACTION_JOB_TYPE
from app.modules.etsy_spy.service import verified_image_url
from app.modules.etsy_spy.design_artifacts import design_media_path
from .model import CamMuseJob, CamMuseCommand
from . import native_placement_review as placement

POLICY = "CAM_MUSE_LATEST_ORIGINAL_V1"
HISTORY = "artworkVersionHistory"
CURRENT = "latestArtworkVersion"
SHA = re.compile(r"^[0-9a-f]{64}$")
ASSET = re.compile(r"^[0-9a-f]{24}$")
JOB = re.compile(r"^[0-9a-f-]{36}$")


def enabled():
    return os.environ.get("CAM_MUSE_LATEST_ARTWORK_ENABLED", "true").strip().lower() == "true"


def contract():
    return {"policy": POLICY, "enabled": enabled(),
            "sourceAuthority": "CAM_saved_native_master_generation",
            "sameOriginalShaRequiresNewVideo": False,
            "newOriginalShaRequiresNewArtworkQa": True,
            "preserveOldDriveMedia": True, "preserveActiveLeases": True,
            "recheckAtClaim": True,
            "instructions": "Use the SHA and Original PNG in the live claim, not a listing-only local cache. On ARTWORK_VERSION_CHANGED refresh CAM discovery; do not retry the stale command or reuse an old hero. Completed videos count only for their own Original SHA. A genuine refusal or operator hold remains in force."}


@dataclass(frozen=True)
class Latest:
    state: str
    job_id: str
    digest: str | None = None
    asset_id: str | None = None
    position: str | None = None
    source_url: str | None = None

    @property
    def path(self):
        return design_media_path(self.job_id, self.asset_id) if self.asset_id else None


def choose(rows):
    """Rows in CAM generation order, newest first; mappings append immutably.

    Explicit retries wait for their saved Native Master. Failed/cancelled
    retries do not replace the last saved design. A newer generation with
    only non-front art is not permission to fall back to an older front.
    """
    for row in rows:
        if row.cancellation_requested or row.status == "failed":
            continue
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
        retry = isinstance(payload.get("retry"), dict)
        if row.status != "completed":
            if retry:
                return Latest("awaiting_original", row.id)
            continue
        assets = payload.get("design_assets")
        native = [a for a in assets if isinstance(a, dict)
                  and a.get("artwork_kind") == "native_master_v2"] if isinstance(assets, list) else []
        if not native:
            if retry:
                return Latest("awaiting_original", row.id)
            continue
        # Never substitute a print PNG, thumbnail, or a malformed Native
        # Master. Front evidence outranks unknown placement in this generation.
        valid = [a for a in native if a.get("method") == "imagegen"
                 and isinstance(a.get("id"), str) and ASSET.fullmatch(a["id"])
                 and isinstance(a.get("output_sha256"), str) and SHA.fullmatch(a["output_sha256"])]
        front = [a for a in valid if a.get("position") == "front"]
        unknown = [a for a in valid if a.get("position") == "unknown"
                   and not any(b["output_sha256"] == a["output_sha256"]
                               and b.get("position") not in ("front","unknown",None) for b in valid)]
        candidates = front or unknown
        if not candidates or not JOB.fullmatch(row.id):
            return Latest("unsupported_placement_or_master", row.id)
        # The upload endpoint appends immutable descriptors in commit order.
        a = candidates[-1]
        src = payload.get("source")
        url = verified_image_url(src.get("url") if isinstance(src, dict) else None)
        return Latest("saved", row.id, a["output_sha256"], a["id"],
                      a["position"], url if url and len(url) <= 1024 else None)
    return None


def load(db, tenant, listing_ids):
    ids = sorted({key for key in listing_ids if key})
    if not enabled() or not ids:
        return {}
    grouped = defaultdict(list)
    for offset in range(0,len(ids),200):
        rows = db.execute(select(P.id,P.entity_id,P.status,P.cancellation_requested,P.payload_json).where(
            P.tenant_id == tenant, P.entity_type == "etsy_listing",
            P.job_type == EXTRACTION_JOB_TYPE, P.entity_id.in_(ids[offset:offset+200]),
            P.cancellation_requested.is_(False), P.status != "failed",
        ).order_by(P.entity_id, P.created_at.desc(),P.id.desc())).all()
        for row in rows:
            grouped[row.entity_id].append(row)
    return {key:choose(grouped[key]) for key in ids}


def prime(db, tenant, ids):
    if not enabled():
        return {}
    found = load(db,tenant,ids)
    db.info.setdefault("muse_latest_artwork",{})[tenant] = found
    return found


def current(db, tenant, job, *, fresh=False):
    if not enabled():
        return True
    cache = db.info.get("muse_latest_artwork",{}).get(tenant,{})
    selected = (cache[job.listing_key] if not fresh and job.listing_key in cache
                else load(db,tenant,[job.listing_key]).get(job.listing_key))
    return selected is None or (selected.state == "saved" and selected.digest == job.artwork_sha256)


def require_current(db, tenant, job):
    if not current(db,tenant,job,fresh=True):
        raise HTTPException(409, {"code":"ARTWORK_VERSION_CHANGED",
                                 "refreshDiscovery":True,
                                 "continueOtherListings":True,
                                 "message":"CAM has a newer Original or a replacement awaiting completion. Do not render the stale revision."})


def revision_marker(selected, old_sha, now, prior=None):
    marker = {"policy": POLICY, "artworkSha256": selected.digest,
              "sourceJobId": selected.job_id, "sourceAssetId": selected.asset_id,
              "pinnedNativeJobId": selected.job_id, "pinnedNativeAssetId": selected.asset_id,
              "priorArtworkSha256": old_sha, "selectedAtUtc": now.isoformat()}
    if isinstance(prior, dict):
        # Returning to exactly the same original does not invalidate a
        # previously accepted source binding or restart its attempts.
        old_path = prior.get("artworkAssetPath") or ""
        parts = old_path.split("/")
        if len(parts) >= 3 and parts[-1] == "original" and JOB.fullmatch(parts[-3]) and ASSET.fullmatch(parts[-2]):
            marker["pinnedNativeJobId"], marker["pinnedNativeAssetId"] = parts[-3], parts[-2]
        elif isinstance(prior.get("receipts",{}).get(CURRENT),dict):
            pin = prior["receipts"][CURRENT]
            if JOB.fullmatch(str(pin.get("pinnedNativeJobId",""))) and ASSET.fullmatch(str(pin.get("pinnedNativeAssetId",""))):
                marker["pinnedNativeJobId"] = pin["pinnedNativeJobId"]
                marker["pinnedNativeAssetId"] = pin["pinnedNativeAssetId"]
    return marker


def pinned_match(db,tenant,job):
    """Resolve only the exact selected immutable source for a rotated job."""
    marker = (getattr(job, "output_receipts", None) or {}).get(CURRENT)
    if not isinstance(marker,dict) or marker.get("policy") != POLICY or marker.get("artworkSha256") != job.artwork_sha256:
        return None,False
    row = db.scalar(select(P).where(P.id == marker.get("pinnedNativeJobId"),
        P.tenant_id == tenant,P.entity_type == "etsy_listing",P.entity_id == job.listing_key,
        P.job_type == EXTRACTION_JOB_TYPE,P.status == "completed",P.cancellation_requested.is_(False)))
    if row is None:
        return None,True
    payload = row.payload_json if isinstance(row.payload_json,dict) else {}
    assets = payload.get("design_assets")
    if not isinstance(assets,list):
        return None,True
    matches = [a for a in assets if isinstance(a,dict) and a.get("output_sha256") == job.artwork_sha256
               and a.get("artwork_kind") == "native_master_v2" and a.get("method") == "imagegen"]
    for a in matches:
        if a.get("id") != marker.get("pinnedNativeAssetId") or a.get("position") not in ("front","unknown"):
            continue
        if a["position"] == "unknown" and any(b.get("position") not in ("front","unknown",None) for b in matches):
            return None,True
        return placement.NativeMatch(row.id,a["id"],a["position"]),True
    return None,True


def _held(job, history, muse, now):
    receipts = job.output_receipts or {}
    if (muse.skips.active(job,now) or receipts.get("videoCapabilityHold")
            or muse._automation_soft_hold_active(receipts)
            or muse._render_retry_wait_seconds(receipts,now=now)):
        return True
    qa = receipts.get("visual_qa")
    notes = [str(qa.get("notes") or "")] if isinstance(qa,dict) else []
    notes.extend(str((c.details or {}).get("notes") or "") for c in history
                 if c.artwork_sha256 == job.artwork_sha256 and c.status == "failed")
    blocked = ("provider_refused","provider refusal","provider refused","safety refusal",
               "content policy","content_policy","rights denied","permission denied",
               "manual hold","operator hold","do not retry","renderer_capability_blocked",
               "fabric_surface_tracking_blocked","print_surface_qa_hold")
    return any(x in n.lower() for n in notes for x in blocked)


def _stamp(value):
    return value.isoformat() if value is not None else None


def _parse(value):
    return datetime.fromisoformat(value) if value else None


def switch(db, tenant, job, selected, now, recovery, muse, locked):
    """Call with current job and ALL active command rows locked command-first."""
    if not selected or selected.state != "saved" or selected.digest == job.artwork_sha256:
        return "unchanged"
    if (job.status == "claimed" or job.lease_hash or job.lease_expires_at
            or not selected.source_url):
        return "deferred"
    history = recovery._history(db,tenant,job.listing_key)
    if any(c.status == "claimed" or c.lease_hash or c.lease_expires_at for c in locked):
        return "deferred"
    if _held(job,history,muse,now):
        return "held"
    receipts = dict(job.output_receipts or {})
    versions = receipts.pop(HISTORY,{})
    if not isinstance(versions,dict):
        return "held"
    versions = dict(versions)
    old_sha = job.artwork_sha256
    previous = versions.get(selected.digest)
    # A previously seen byte-identical version must never obtain a fresh
    # retry budget. Restore only its exact previous source and receipt state.
    if previous and (not isinstance(previous,dict)
                     or previous.get("sourceImageUrl") != selected.source_url
                     or not isinstance(previous.get("receipts"),dict)):
        return "held"
    if old_sha:
        versions[old_sha] = {"artworkSha256":old_sha,"artworkAssetPath":job.artwork_asset_path,
            "sourceImageUrl":job.product_image_url,"status":job.status,"sourceState":job.source_state,
            "attempts":job.attempts,"submittedAt":_stamp(job.submitted_at),
            "completedAt":_stamp(job.completed_at),"receipts":receipts,"archivedAtUtc":now.isoformat()}
    for command in locked:
        if command.status == "pending" and command.artwork_sha256 != selected.digest:
            recovery.terminal_command(command,"failed","artwork_version_superseded",now)
    job.artwork_sha256 = selected.digest
    job.product_image_url = selected.source_url
    marker = revision_marker(selected,old_sha,now,previous)
    if previous:
        job.artwork_asset_path = previous.get("artworkAssetPath")
        job.status = previous["status"]
        job.source_state = previous["sourceState"]
        job.attempts = previous["attempts"]
        job.submitted_at = _parse(previous.get("submittedAt"))
        job.completed_at = _parse(previous.get("completedAt"))
        job.output_receipts = {**previous["receipts"],HISTORY:versions,CURRENT:marker}
    else:
        job.artwork_asset_path = None
        job.status = job.source_state = "qa_hold"
        job.attempts = 0
        job.submitted_at = job.completed_at = None
        job.output_receipts = {HISTORY:versions,CURRENT:marker}
    job.updated_at = now
    return "restored" if previous else "advanced"


def reconcile(db,tenant,jobs,now,recovery,muse):
    stats = {"artworkVersionsAdvanced":0,"artworkVersionsRestored":0,
             "artworkVersionsDeferred":0,"artworkVersionsHeld":0,
             "artworkVersionsAwaitingOriginal":0}
    if not enabled():
        return stats
    selected = prime(db,tenant,[j.listing_key for j in jobs])
    for candidate in jobs:
        latest = selected.get(candidate.listing_key)
        if latest is None or (latest.state == "saved" and latest.digest == candidate.artwork_sha256):
            continue
        if latest.state != "saved":
            stats["artworkVersionsAwaitingOriginal"] += 1
            continue
        # Command-then-job lock order matches claim/complete. Never wait for
        # or steal another worker's active lease.
        q = select(CamMuseCommand).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.listing_key == candidate.listing_key,
            CamMuseCommand.status.in_(("pending","claimed")),
            CamMuseCommand.action.in_(("create_video","review_artwork")),
        ).order_by(CamMuseCommand.command_id)
        locked = db.scalars(q.with_for_update(skip_locked=True)).all()
        job = recovery._lock_job(db,tenant,candidate.listing_key)
        if job is None or {c.command_id for c in db.scalars(q).all()} != {c.command_id for c in locked}:
            stats["artworkVersionsDeferred"] += 1
            continue
        # A newer extraction may have committed between the batch read and
        # locking the job. Recheck the authoritative generation under lock.
        fresh = load(db,tenant,[job.listing_key]).get(job.listing_key)
        selected[job.listing_key] = fresh
        if not fresh or fresh.state != "saved":
            stats["artworkVersionsAwaitingOriginal"] += 1
            continue
        outcome = switch(db,tenant,job,fresh,now,recovery,muse,locked)
        if outcome != "unchanged":
            stats["artworkVersions"+outcome.capitalize()] += 1
    return stats