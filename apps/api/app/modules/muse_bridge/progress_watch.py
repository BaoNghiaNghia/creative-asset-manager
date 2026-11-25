"""Read-only video progress diagnostics; no claims, restarts or renderer calls.

Require a continuous sequence of CAM-observed ready-queue samples and real
API observations. A poll or repeated upload ACK is not a new saved video.
"""
from datetime import timedelta
from sqlalchemy import or_, select
from .model import CamMuseCommand, CamMuseJob
from . import observability as obs
from app.modules.application_logs.model import ApplicationLogModel

POLICY = "CAM_MUSE_PROGRESS_WATCHDOG_V1"
WINDOW_SECONDS = 300
LOOKBACK_SECONDS = 900
MAX_SAMPLE_GAP_SECONDS = 300
MAX_LATEST_SAMPLE_AGE = 240


def contract():
    return {
        "policy": POLICY, "readyWithoutVideoProgressSeconds": WINDOW_SECONDS,
        "diagnosticsField": "videoProgress",
        "diagnosticsPath": "/api/v1/muse-bridge/automation/diagnostics",
        "alertConfirmations": 2,
        "observesOnly": True,
        "doesNotReleaseLeasesOrRestartRenderer": True,
        "instructions": (
            "Use the CAM-selected execution contract. A ready queue without video "
            "progress requires checking runner routing and real claim errors. "
            "Do not infer a missing review from an empty local cache. Honor all "
            "provider cooldowns and genuine holds. Never regenerate a verified ratio."
        ),
    }


def _age(now, stamp):
    return max(0, int((now - obs.aware(stamp)).total_seconds())) if stamp else None


def _sample_valid(row):
    payload = row.payload_json or {}
    return (payload.get("skipped") is False
            and payload.get("failed") is False
            and all(type(payload.get(k)) is int and payload[k] >= 0
                    for k in ("readyVideoListings", "activeVideoListings")))


def evaluate(report, samples, events, preferred, now):
    """Inputs: recent tenant-scoped CAM sweep/API rows, newest or oldest order."""
    result = {"policy": POLICY, "state": "WAITING_FOR_EVIDENCE",
              "preferredWorker": preferred, "readyVideoListings": None,
              "activeVideoListings": None, "continuousReadyWithoutLeaseSeconds": 0,
              "withoutVideoProgressSeconds": 0, "discoveryPolls": 0,
              "claimRejections": 0, "lastVideoClaimAgeSeconds": None,
              "lastNewVideoStoredAgeSeconds": None,
              "readOnly": True, "rendererProgressIndependentlyVerified": False}
    recovery = report.get("queueRecovery") or {}
    if not recovery.get("enabled"):
        result["state"] = "DISABLED"
        return result
    if recovery.get("status") != "RECENT":
        return result
    samples = sorted(samples, key=lambda r: obs.aware(r.received_at), reverse=True)
    if not samples:
        return result
    latest = samples[0]
    if not _sample_valid(latest) or _age(now, latest.received_at) > MAX_LATEST_SAMPLE_AGE:
        return result
    counts = latest.payload_json
    result.update(readyVideoListings=counts["readyVideoListings"],
                  activeVideoListings=counts["activeVideoListings"])
    if counts["activeVideoListings"]:
        result["state"] = "VIDEO_LEASE_ACTIVE"
        return result
    if counts["readyVideoListings"] == 0:
        result["state"] = "NO_READY_VIDEO"
        return result
    circuit = (report.get("rendererCircuit") or {}).get("state")
    if circuit == "open":
        result["state"] = "RENDERER_CIRCUIT_OPEN"
        return result
    if circuit == "half_open":
        result["state"] = "RENDERER_PROBE_PENDING"
        return result
    if circuit != "closed":
        return result
    worker = next((w for w in report.get("workers", []) if w.get("workerId")==preferred), None)
    if worker is None or worker.get("executionReadiness", {}).get("ready") is False or worker.get("cooldownStateUnavailable"):
        result["state"] = "NO_AVAILABLE_RENDER_WORKER"
        return result
    if (worker.get("cooldown") or {}).get("cooldownActive"):
        result["state"] = "PROVIDER_COOLDOWN"
        return result
    oldest = obs.aware(latest.received_at)
    for row in samples[1:]:
        stamp = obs.aware(row.received_at)
        if ((oldest-stamp).total_seconds() > MAX_SAMPLE_GAP_SECONDS
                or not _sample_valid(row)
                or not row.payload_json["readyVideoListings"]
                or row.payload_json["activeVideoListings"]):
            break
        oldest = stamp
    result["continuousReadyWithoutLeaseSeconds"] = int((obs.aware(latest.received_at)-oldest).total_seconds())
    claims, stored, polls, rejects = [], [], [], []
    offers = []
    for row in events:
        event = row.payload_json or {}
        stamp = obs.aware(row.received_at)
        if (stamp < oldest or stamp > now or event.get("workerId") != preferred
                or event.get("origin") != "cam_observed"):
            continue
        status, route = event.get("httpStatus"), event.get("route")
        if type(status) is not int:
            continue
        if 200 <= status < 300:
            if (route in ("command_poll", "video_poll") and event.get("videoDiscoveryAllowed") is True
                    and type(event.get("videoCandidatesOffered")) is int):
                polls.append(stamp)
                if event["videoCandidatesOffered"] > 0:
                    offers.append(stamp)
            elif route == "jobs_claim" or (route == "command_claim" and event.get("phase") == "create_video"):
                claims.append(stamp)
            elif route in ("jobs_videos_vertical", "jobs_videos_horizontal") and event.get("newVideoStorageVerified") is True:
                stored.append(stamp)
        elif status in (409, 422) and route in ("command_claim", "jobs_claim"):
            rejects.append(stamp)
    last_claim = max(claims, default=None)
    last_store = max(stored, default=None)
    result.update(discoveryPolls=len(polls), claimRejections=len(rejects),
                  lastVideoClaimAgeSeconds=_age(now,last_claim),
                  lastNewVideoStoredAgeSeconds=_age(now,last_store))
    last_progress = max([oldest]+claims+stored)
    no_progress = int((obs.aware(latest.received_at)-last_progress).total_seconds())
    result["withoutVideoProgressSeconds"] = max(0,no_progress)
    if result["continuousReadyWithoutLeaseSeconds"] < WINDOW_SECONDS or no_progress < WINDOW_SECONDS:
        result["state"] = "RECENT_OR_UNCONFIRMED_PROGRESS"
        return result
    # Require polling evidence after the last real video progress. Replays of
    # worker-reported telemetry do not count, and a standby GET is not work.
    polls = [p for p in polls if p >= last_progress]
    offers = [p for p in offers if p >= last_progress]
    result["pollsOfferingVideo"] = len(offers)
    if (len(polls) >= 2 and _age(now,max(polls)) <= obs.STALE_SECONDS
            and (max(polls)-min(polls)).total_seconds() >= 240):
        if len([r for r in rejects if r >= last_progress]) >= 2:
            result["state"] = "READY_QUEUE_CLAIM_REJECTIONS"
        elif len(offers) >= 2 and (max(offers)-min(offers)).total_seconds() >= 240:
            result["state"] = "READY_QUEUE_POLLING_WITHOUT_VIDEO_PROGRESS"
        elif not offers:
            result["state"] = "READY_QUEUE_DISCOVERY_MISMATCH"
        else:
            result["state"] = "READY_QUEUE_PROGRESS_UNCONFIRMED"
    elif worker.get("status") in ("HEARTBEAT_RECENT","API_ACTIVE_HEARTBEAT_UNCONFIRMED") and not polls:
        result["state"] = "READY_QUEUE_NO_OBSERVED_DISCOVERY"
    else:
        result["state"] = "READY_QUEUE_PROGRESS_UNCONFIRMED"
    return result


def diagnostics(db, tenant, report, preferred, now):
    if not obs.enabled():
        return {"policy": POLICY, "state": "DISABLED", "readOnly": True}
    cutoff = now-timedelta(seconds=LOOKBACK_SECONDS)
    samples = db.scalars(obs._rows(db,tenant,now,
        ApplicationLogModel.event_type=="CAM_RECOVERY_SWEEP",
        ApplicationLogModel.received_at>=cutoff,
        ApplicationLogModel.received_at<=now).order_by(
            ApplicationLogModel.received_at.desc()).limit(12)).all()
    events = []
    if preferred and samples and _sample_valid(samples[0]):
        events = db.scalars(obs._rows(db,tenant,now,
            ApplicationLogModel.event_type=="CAM_API_RESULT",
            ApplicationLogModel.payload_json["workerId"].as_string()==preferred,
            ApplicationLogModel.payload_json["route"].as_string().in_(
                ("command_poll","video_poll","command_claim","jobs_claim",
                 "jobs_videos_vertical","jobs_videos_horizontal")),
            ApplicationLogModel.received_at>=cutoff,
            ApplicationLogModel.received_at<=now).order_by(
                ApplicationLogModel.received_at.desc()).limit(240)).all()
    result = evaluate(report,samples,events,preferred,now)
    # A claim may arrive between recovery sweeps. Suppress ready-queue alerts
    # when a real durable video lease is now active. No writes or lease repairs.
    if result["state"].startswith("READY_QUEUE_"):
        job_active = db.scalar(select(CamMuseJob.listing_key).where(
            CamMuseJob.tenant_id == tenant, CamMuseJob.status == "claimed",
            or_(CamMuseJob.lease_expires_at.is_(None), CamMuseJob.lease_expires_at > now),
        ).limit(1))
        cmd_active = db.scalar(select(CamMuseCommand.command_id).where(
            CamMuseCommand.tenant_id == tenant, CamMuseCommand.status == "claimed",
            CamMuseCommand.phase == "create_video", CamMuseCommand.action != "execute_task",
            or_(CamMuseCommand.lease_expires_at.is_(None), CamMuseCommand.lease_expires_at > now),
        ).limit(1))
        if job_active is not None or cmd_active is not None:
            result["state"] = "VIDEO_LEASE_ACTIVE"
            result["activeVideoLeaseObservedSinceSample"] = True
    return result