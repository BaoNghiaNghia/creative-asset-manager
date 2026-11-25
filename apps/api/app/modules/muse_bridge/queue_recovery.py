"""Bounded CAM queue maintenance; no model calls, no QA verdicts, no media writes.

A PostgreSQL advisory lock serializes periodic sweep instances. Individual
command/job rows are locked with SKIP LOCKED so live workers are not disturbed.
The historical external dispatcher may coexist: new commands serialize on
the same listing row as create_operator_command.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select, text
from .model import CamMuseCommand, CamMuseJob
from . import skip_reconciliation as skips
from . import source_qa_recheck as recheck
from . import latest_artwork

POLICY = "CAM_SMART_QUEUE_RECOVERY_V1"
QUEUE_CAP = 12
SWEEP_INTERVAL = 120
MAX_COMMAND_CLAIMS = 5
MAX_JOB_CLAIMS = 5


def aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def active_lease(row, now):
    expiry = aware(row.lease_expires_at)
    # A malformed claimed lease is not safe to steal.
    return row.status == "claimed" and (expiry is None or expiry > now)


def expired_lease(row, now):
    expiry = aware(row.lease_expires_at)
    return row.status == "claimed" and expiry is not None and expiry <= now


def terminal_command(command, status, reason, now, *, linked=False):
    command.status = status
    command.lease_hash = None
    command.lease_expires_at = None
    command.finished_at = now
    command.updated_at = now
    command.details = {
        **(command.details or {}),
        "queueRecovery": {"policy": POLICY, "reason": reason, "atUtc": now.isoformat()},
        "videoReceiptNotCamLinked": not linked,
    }


def reconcile_command(command, job, now, verified_ratios):
    """Return recovery reason, if any; never touch an active or malformed lease."""
    if active_lease(command, now) or (job is not None and active_lease(job, now)):
        return None
    if command.status not in ("pending", "claimed"):
        return None
    if job is None:
        # Generic tasks are outside the garment dispatcher.
        return None
    if job.artwork_sha256 != command.artwork_sha256:
        terminal_command(command, "failed", "artwork_changed", now)
        return "artwork_changed"
    if job.status in ("submitted", "completed") and verified_ratios(job) == {"9:16", "16:9"}:
        terminal_command(command, "receipt_submitted", "both_videos_already_verified", now,
                         linked=bool((job.output_receipts or {}).get("camLinked")))
        return "both_videos_already_verified"
    required = (command.details or {}).get("requiredVideoAspectRatios")
    if (command.phase == "create_video" and required in (["9:16"], ["16:9"])
            and required[0] in verified_ratios(job)):
        reason = "required_vertical_already_verified" if required == ["9:16"] else "required_horizontal_already_verified"
        terminal_command(command, "receipt_submitted", reason, now,
                         linked=bool((job.output_receipts or {}).get("camLinked")))
        command.details = {**(command.details or {}),
                           "completedVideoAspectRatios": required,
                           "allVideoAspectsComplete": verified_ratios(job) == {"9:16", "16:9"},
                           "deferredVideoAspectRatios": [r for r in ("9:16", "16:9") if r not in verified_ratios(job)]}
        return reason
    qa = (job.output_receipts or {}).get("visual_qa")
    if command.phase == "review_artwork" and isinstance(qa, dict) and qa.get("artwork_sha256") == job.artwork_sha256:
        if command.action == "review_artwork" and qa.get("verdict") == "pass":
            terminal_command(command, "reviewed", "artwork_review_already_saved", now)
            return "artwork_review_already_saved"
        if qa.get("verdict") != "pass":
            terminal_command(command, "failed", "artwork_review_on_hold", now)
            return "artwork_review_on_hold"
    if command.attempts >= recheck.claim_limit(command) or (
        command.phase == "create_video" and job.attempts >= MAX_JOB_CLAIMS
    ):
        terminal_command(command, "failed", "attempt_limit", now)
        return "attempt_limit"
    if expired_lease(command, now):
        command.status = "pending"
        command.lease_hash = None
        command.lease_expires_at = None
        command.updated_at = now
        command.details = {**(command.details or {}), "queueRecovery": {
            "policy": POLICY, "reason": "expired_command_lease", "atUtc": now.isoformat(),
        }}
        return "expired_command_lease"
    return None


def can_refill(job, history, now, muse):
    """Respect source/QA holds; allow recovery from obsolete geometry and transport errors."""
    if active_lease(job, now) or job.attempts >= MAX_JOB_CLAIMS:
        return False
    r = job.output_receipts or {}
    if (skips.active(job, now) or r.get("videoCapabilityHold") or muse._automation_soft_hold_active(r)
            or muse._render_retry_wait_seconds(r, now=now)):
        return False
    history = [c for c in history if c.artwork_sha256 == job.artwork_sha256]
    if job.status == "qa_hold":
        # Approved one-time source-scope rechecks have their own bounded lane.
        # Never manufacture a second review command after that lane is used.
        if recheck.marker(job):
            return False
        # An actual negative review is not permission to review repeatedly.
        if muse._qa_record(job) is not None:
            return False
        # Bound failed review attempts across command identities too.
        if sum(c.attempts for c in history if c.phase == "review_artwork") >= 5:
            return False
    # Bound recovery even when failures occur before video-job claim.
    if sum((c.details or {}).get("origin") == POLICY for c in history) >= 3:
        return False
    if history:
        last = history[0]
        notes = str((last.details or {}).get("notes") or "").lower()
        if any(mark in notes for mark in ("rights denied", "permission denied", "manual hold",
                                          "operator hold", "do not retry",
                                          "renderer_capability_blocked",
                                          "fabric_surface_tracking_blocked",
                                          "print_surface_qa_hold", "garment_mismatch_hold")):
            return False
        if last.status == "failed" and aware(last.updated_at) > now - timedelta(minutes=5):
            return False
    return True


def _lock_job(db, tenant, key):
    return db.scalar(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant, CamMuseJob.listing_key == key,
    ).with_for_update(skip_locked=True).execution_options(populate_existing=True))


def _history(db, tenant, key):
    return db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant, CamMuseCommand.listing_key == key,
    ).order_by(CamMuseCommand.created_at.desc()).limit(30)).all()


def reconcile_queue(db, tenant, now=None, *, cap=QUEUE_CAP):
    from . import router as muse
    now = now or datetime.now(timezone.utc)
    stats = {"policy": POLICY, "closed": 0, "expiredCommandsRecovered": 0,
             "expiredJobsReleased": 0, "newVideoCommands": 0,
             "newReviewCommands": 0, "eligibleInflight": 0, "cap": cap,
             "newCommands": [], "partialQaMetadataRepaired": 0,
             "sourceScopeRechecksQueued": 0, "skipped": False}
    if db.get_bind().dialect.name == "postgresql":
        lock_key = int.from_bytes(hashlib.sha256(
            ("cam-muse-queue-recovery:" + tenant).encode()).digest()[:8], "big", signed=True)
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key}):
            stats["skipped"] = True
            return stats
    jobs = db.scalars(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
    ).order_by(CamMuseJob.picked_at.asc()).limit(3000)).all()
    active = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.status.in_(("pending", "claimed")),
        CamMuseCommand.action.in_(("create_video", "review_artwork")),
    ).limit(3000)).all()
    by_listing = {j.listing_key: j for j in jobs}
    for candidate in active:
        if active_lease(candidate, now):
            continue
        job = by_listing.get(candidate.listing_key)
        if job is not None and active_lease(job, now):
            continue
        if not (candidate.attempts >= recheck.claim_limit(candidate) or expired_lease(candidate, now)
                or (job is not None and (job.status in ("submitted", "completed")
                    or (candidate.phase == "review_artwork" and isinstance((job.output_receipts or {}).get("visual_qa"), dict))
                    or job.artwork_sha256 != candidate.artwork_sha256
                    or (candidate.phase == "create_video"
                        and (job.attempts >= 5 or (
                            (candidate.details or {}).get("requiredVideoAspectRatios") in (["9:16"], ["16:9"])
                            and set((candidate.details or {})["requiredVideoAspectRatios"]) <= muse._verified_video_ratios(job))))))):
            continue
        cmd = db.scalar(select(CamMuseCommand).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.command_id == candidate.command_id,
        ).with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if cmd is None:
            continue
        if cmd.listing_key:
            job = _lock_job(db, tenant, cmd.listing_key)
            if job is None:
                continue
        else:
            job = None
        result = reconcile_command(cmd, job, now, muse._verified_video_ratios)
        if result == "expired_command_lease":
            stats["expiredCommandsRecovered"] += 1
        elif result:
            stats["closed"] += 1
    db.flush()
    for candidate in jobs:
        if not expired_lease(candidate, now):
            continue
        job = _lock_job(db, tenant, candidate.listing_key)
        if job is None or not expired_lease(job, now):
            continue
        commands = _history(db, tenant, job.listing_key)
        if any(active_lease(c, now) for c in commands):
            continue
        # Clear only a truly expired lease, never reset attempts or receipts.
        job.status = "pending"
        job.lease_hash = None
        job.lease_expires_at = None
        job.updated_at = now
        stats["expiredJobsReleased"] += 1
    db.flush()
    # Select the newest saved Native Master after ordinary expired-lease
    # recovery. Revision changes never touch an active claim or old media.
    import sys
    stats.update(latest_artwork.reconcile(db, tenant, jobs, now, sys.modules[__name__], muse))
    db.flush()
    # Historical partial uploads predate the first-aspect QA metadata. Only
    # backfill missing pending states, never manufacture a review verdict.
    for candidate in jobs:
        if active_lease(candidate, now) or len(muse._verified_video_ratios(candidate)) != 1:
            continue
        rec = candidate.output_receipts or {}
        if all(key in rec for key in ("videoContentQa", "videoGarmentQa", "camLinked")):
            continue
        job = _lock_job(db, tenant, candidate.listing_key)
        if job is None or active_lease(job, now) or len(muse._verified_video_ratios(job)) != 1:
            continue
        if any(active_lease(c, now) for c in _history(db, tenant, job.listing_key)):
            continue
        receipts = dict(job.output_receipts or {})
        receipts.setdefault("videoContentQa", "pending_review")
        receipts.setdefault("videoGarmentQa", "pending_independent_visual_review")
        receipts.setdefault("camLinked", False)
        job.output_receipts = receipts
        stats["partialQaMetadataRepaired"] += 1
    db.flush()
    active = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.status.in_(("pending", "claimed")),
        CamMuseCommand.action.in_(("create_video", "review_artwork")),
    ).limit(3000)).all()
    by_active = defaultdict(list)
    occupied = set()
    review_occupied = set()
    ready_video = set()
    # Count durable leases separately from merely eligible queued work.
    active_video = {j.listing_key for j in jobs if active_lease(j, now)}
    active_listings = set(active_video)
    for c in active:
        by_active[c.listing_key].append(c)
        j = by_listing.get(c.listing_key)
        if j is None:
            continue
        if active_lease(c, now) or active_lease(j, now):
            active_listings.add(c.listing_key)
            if c.phase == "create_video":
                active_video.add(c.listing_key)
            occupied.add(c.listing_key)
            if c.phase == "review_artwork":
                review_occupied.add(c.listing_key)
        elif c.attempts < recheck.claim_limit(c):
            phase = muse._eligible_phase(db, tenant, j, c)
            if phase:
                occupied.add(c.listing_key)
                if phase == "review_artwork":
                    review_occupied.add(c.listing_key)
                elif phase == "create_video":
                    ready_video.add(c.listing_key)
    stats["heldPendingListings"] = len({c.listing_key for c in active
        if c.status == "pending" and c.phase == "create_video"
        and c.listing_key in by_listing and c.listing_key not in occupied
        and skips.active(by_listing[c.listing_key], now)})
    stats["eligibleInflight"] = len(occupied)
    circuit = renderer_circuit(db, tenant, now)
    # Reserve review work even while the video queue is saturated. Existing
    # video commands drain naturally; never cancel a valid pending command
    # or active lease just to reach the new target.
    review_reserve = min(4, max(1, cap // 3))
    stats["reviewReserve"] = review_reserve
    stats["videoTarget"] = cap - review_reserve
    # Give partially saved videos priority, then approved video work, then
    # unreviewed artwork. No fresh artwork verdict is manufactured here.
    jobs.sort(key=lambda j: (
        muse._video_aspect_priority(set(muse._job_required_aspects(j)) -
                                   muse._verified_video_ratios(j)),
        0 if len(muse._verified_video_ratios(j)) == 1 else
        1 if j.status == "pending" else 2,
        aware(j.picked_at), j.listing_key,
    ))
    review_jobs = [j for j in jobs if j.status == "qa_hold"]
    review_jobs.sort(key=lambda j: (not bool((j.output_receipts or {}).get(latest_artwork.CURRENT)),
                                    not recheck.pending(j), aware(j.picked_at), j.listing_key))
    video_jobs = [j for j in jobs if j.status == "pending"]
    # First reserve review slots, then fill remaining capacity with videos.
    # If video work is scarce, lend spare slots to review. A full preexisting
    # video queue can temporarily exceed cap only by the review reserve.
    schedule = ([("reserve", j) for j in review_jobs]
                + [("video", j) for j in video_jobs]
                + [("extra_review", j) for j in review_jobs])
    for lane, candidate in schedule:
        if lane == "reserve" and len(review_occupied) >= review_reserve:
            continue
        if lane != "reserve" and len(occupied) >= cap:
            continue
        if lane == "video" and circuit["open"]:
            continue
        if candidate.listing_key in by_active or candidate.status not in ("pending", "qa_hold"):
            continue
        if not candidate.artwork_sha256 or not muse.HEX64.fullmatch(candidate.artwork_sha256):
            continue
        if not latest_artwork.current(db, tenant, candidate):
            continue
        # Cheap predicates before acquiring the listing lock.
        scope_candidate = candidate.status == "qa_hold" and recheck.pending(candidate)
        if candidate.status == "qa_hold" and (not candidate.product_image_url
                or (muse._qa_record(candidate) and not scope_candidate)):
            continue
        if not scope_candidate and not can_refill(candidate, [], now, muse):
            continue
        job = _lock_job(db, tenant, candidate.listing_key)
        if job is None or job.status not in ("pending", "qa_hold"):
            continue
        history = _history(db, tenant, job.listing_key)
        if db.scalar(select(CamMuseCommand.command_id).where(
            CamMuseCommand.tenant_id == tenant,
            CamMuseCommand.listing_key == job.listing_key,
            CamMuseCommand.status.in_(("pending", "claimed")),
        ).limit(1)) is not None:
            continue
        command_id = str(uuid4())
        prepared = None
        if job.status == "qa_hold" and recheck.pending(job):
            if lane == "video":
                continue
            prepared = recheck.prepared_receipts(
                job, history, muse._review_native_asset(db, tenant, job), now, command_id)
            if prepared is None:
                continue
            phase = "review_artwork"
        else:
            if not can_refill(job, history, now, muse):
                continue
            phase = muse._eligible_phase(db, tenant, job, SimpleNamespace(
                action="create_video", artwork_sha256=job.artwork_sha256))
        if not phase or (lane == "video" and phase != "create_video") or (
                lane != "video" and phase != "review_artwork"):
            continue
        identity = hashlib.sha256((
            job.listing_key + ":" + job.artwork_sha256 + ":" +
            (history[0].command_id if history else "first")
        ).encode()).hexdigest()[:40]
        command = CamMuseCommand(
            tenant_id=tenant, command_id=command_id, listing_key=job.listing_key,
            action=phase, request_id=("cam-source-scope-recheck-" if prepared else "cam-smart-recovery-") + identity,
            artwork_sha256=job.artwork_sha256, status="pending", phase=phase,
            attempts=0, created_at=now, updated_at=now,
            details={"origin": POLICY, "rights_verified": False,
                     "note": "Recover eligible queue work. Use exact live per-listing contract; preserve saved media.",
                     "previousCommandId": history[0].command_id if history else None},
        )
        if prepared:
            command.details = {**command.details, recheck.KEY: {
                "policy": recheck.POLICY,
                "priorReviewAttempts": prepared[recheck.KEY]["priorReviewAttempts"],
                "priorCommandId": prepared[recheck.KEY]["priorCommandId"],
            }}
            job.output_receipts = prepared
            job.updated_at = now
            stats["sourceScopeRechecksQueued"] += 1
        db.add(command)
        db.flush()
        occupied.add(job.listing_key)
        by_active[job.listing_key].append(command)
        if phase == "review_artwork":
            review_occupied.add(job.listing_key)
        elif phase == "create_video":
            ready_video.add(job.listing_key)
        stats["newReviewCommands" if phase == "review_artwork" else "newVideoCommands"] += 1
        stats["newCommands"].append({"listingId": job.listing_key,
                                     "commandId": command.command_id, "phase": phase})
    stats["eligibleInflightAfter"] = len(occupied)
    stats["reviewInflightAfter"] = len(review_occupied)
    stats["videoInflightAfter"] = len(occupied - review_occupied)
    stats["readyVideoListings"] = len(ready_video - active_listings)
    stats["activeVideoListings"] = len(active_video)
    stats["transitionalOverflow"] = max(0, len(occupied) - cap)
    db.commit()
    return stats


CIRCUIT_HISTORY_MINUTES = 30


def renderer_circuit_from_events(events, now):
    """Open after two distinct failures within 10 min; probe once after 5 min.

    Replay a bounded history so the first failure aging out of the 10-minute
    trigger window cannot prematurely close an already-open circuit.
    A subsequent transient failure before a verified upload reopens it,
    including a failed probe of the same listing. No permanent provider hold.
    """
    floor = now - timedelta(minutes=CIRCUIT_HISTORY_MINUTES)
    recent = []
    until = None
    for stamp, key, kind in sorted(
            (e for e in events if floor <= e[0] <= now),
            key=lambda e: (e[0], e[2] == "success")):
        if kind == "success":
            recent = []
            until = None
        elif kind == "failure" and key:
            recent = [(t, k) for t, k in recent if t >= stamp - timedelta(minutes=10)]
            recent.append((stamp, key))
            if until is not None or len({k for _, k in recent}) >= 2:
                until = stamp + timedelta(minutes=5)
    distinct = len({key for t, key in recent if t >= now - timedelta(minutes=10)})
    opened = until is not None and until > now
    probe = until is not None and not opened
    return {
        "policy": "CAM_RENDERER_CIRCUIT_BREAKER_V2",
        "state": "open" if opened else "half_open" if probe else "closed",
        "open": opened,
        "probeRequired": probe,
        "maxVideoCandidatesPerPoll": 0 if opened else 1 if probe else None,
        "reason": "worker_reported_upstream_failures" if opened else
                  "single_video_recovery_probe" if probe else None,
        "distinctFailedListings": distinct,
        "retryAfterSeconds": max(1, int((until - now).total_seconds() + .999999)) if opened else 0,
        "retryAtUtc": until.isoformat() if opened else None,
        "reviewArtworkAllowed": True,
        "existingLeasesMayFinish": True,
        "providerOutageIndependentlyVerified": False,
    }


def renderer_circuit(db, tenant, now=None):
    now = now or datetime.now(timezone.utc)
    if not recovery_enabled():
        return renderer_circuit_from_events([], now)
    rows = db.scalars(select(CamMuseCommand).where(
        CamMuseCommand.tenant_id == tenant,
        CamMuseCommand.phase == "create_video",
        CamMuseCommand.updated_at >= now - timedelta(minutes=CIRCUIT_HISTORY_MINUTES),
    ).order_by(CamMuseCommand.updated_at.desc()).limit(100)).all()
    events = []
    for c in rows:
        marker = (c.details or {}).get("lastRendererTransientFailure")
        if isinstance(marker, dict) and marker.get("httpStatus") in (502, 503, 504):
            try:
                ts = aware(datetime.fromisoformat(marker["atUtc"].replace("Z", "+00:00")))
                events.append((ts, c.listing_key, "failure"))
            except (ValueError, TypeError, AttributeError, KeyError):
                pass
    # A verified upload of even one aspect is a useful successful delivery.
    # Do not count command reconciliation as a fresh render; the upload
    # timestamp is written only by the verified MP4 storage path.
    from . import router as muse
    jobs = db.scalars(select(CamMuseJob).where(
        CamMuseJob.tenant_id == tenant,
        CamMuseJob.updated_at >= now - timedelta(minutes=CIRCUIT_HISTORY_MINUTES),
    ).limit(3000)).all()
    for job in jobs:
        try:
            marker = (job.output_receipts or {}).get("videoLastStoredAtUtc")
            ts = aware(datetime.fromisoformat(marker.replace("Z", "+00:00")))
            if now - timedelta(minutes=CIRCUIT_HISTORY_MINUTES) <= ts <= now and muse._verified_video_ratios(job):
                events.append((ts, job.listing_key, "success"))
        except (TypeError, ValueError, AttributeError):
            continue
    return renderer_circuit_from_events(events, now)


def recovery_enabled():
    from .router import _continuous_gen_enabled
    return (_continuous_gen_enabled() and
            os.getenv("CAM_MUSE_QUEUE_RECOVERY_ENABLED", "true").strip().lower() == "true"
            and len(os.getenv("CAM_MUSE_BRIDGE_TOKEN", "")) >= 48
            and len(os.getenv("CAM_MUSE_TENANT_ID", "")) >= 2)


def run_configured_recovery():
    from app.core.database import SessionLocal
    if not recovery_enabled():
        return None
    with SessionLocal() as db:
        return reconcile_queue(db, os.environ["CAM_MUSE_TENANT_ID"])