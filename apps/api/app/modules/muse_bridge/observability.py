"""Tenant-scoped Muse observability on existing application_logs storage.
Advisory telemetry cannot claim, renew, release or complete a job.
No request bodies, bearer tokens, leases, provider payloads or free-text logs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from threading import BoundedSemaphore
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.scout_log_ingress import ScoutTelemetryLimiter
from app.modules.application_logs.model import ApplicationLogModel, LogApplicationModel
from .model import CamMuseCommand, CamMuseJob

POLICY = "CAM_MUSE_OBSERVABILITY_V2"
SLUG = "cam-muse-observability-v2"
RETENTION_DAYS = 7
HEARTBEAT_SECONDS = 60
STALE_SECONDS = 180
RECOVERY_STALE_SECONDS = 360
LOGGER = logging.getLogger("uvicorn.error")
WORKERS = ("legacy", "muse-01", "muse-02")
INGRESS_LIMITER = ScoutTelemetryLimiter(rate=0.5, burst=8)
API_WRITE_SLOTS = BoundedSemaphore(4)

EventType = Literal[
    "RUN_STARTED", "WORKER_HEARTBEAT", "POLL_SUCCESS", "QUEUE_EMPTY",
    "COMMAND_CLAIMED", "JOB_CLAIMED", "HERO_READY", "RENDER_STARTED",
    "RENDER_FAILED", "COOLDOWN_STARTED", "COOLDOWN_ENDED", "VIDEO_UPLOADED",
    "CAM_ACK_RECEIVED", "RUN_FINISHED", "LOG_REPLAYED", "WORKER_ERROR",
]
ErrorCode = Literal[
    "NONE", "NETWORK_ERROR", "TIMEOUT", "PROVIDER_RATE_LIMIT",
    "VIDEO_DAILY_BUDGET_EXHAUSTED", "PROVIDER_UNAVAILABLE",
    "LEASE_REJECTED", "INVALID_CONTRACT", "ARTWORK_UNAVAILABLE",
    "QA_HOLD", "GARMENT_MISMATCH", "UPLOAD_FAILED", "CAM_ACK_FAILED",
    "RENDERER_UNAVAILABLE", "INTERNAL_ERROR", "UNKNOWN",
]
WorkerState = Literal["idle", "polling", "reviewing", "rendering", "uploading",
                      "cooldown", "blocked", "stopping"]
Aspect = Literal["9:16", "16:9"]


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def enabled():
    return os.getenv("CAM_MUSE_OBSERVABILITY_ENABLED", "true").lower().strip() == "true"


class WorkerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eventId: UUID
    eventType: EventType
    occurredAtUtc: datetime
    runId: UUID | None = None
    commandId: UUID | None = None
    listingId: str | None = Field(default=None, pattern=r"^[0-9]{5,20}$")
    aspectRatio: Aspect | None = None
    state: WorkerState | None = None
    errorCode: ErrorCode | None = None
    httpStatus: int | None = Field(default=None, ge=100, le=599)
    retryAfterSeconds: int | None = Field(default=None, ge=0, le=86400)
    durationMs: int | None = Field(default=None, ge=0, le=86400000)

    @field_validator("occurredAtUtc")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("UTC offset required")
        return value.astimezone(timezone.utc)


class WorkerEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[WorkerEvent] = Field(min_length=1, max_length=20)


def application_id(tenant):
    return str(uuid5(NAMESPACE_URL, POLICY + ":" + tenant))


def _application(db, tenant):
    aid = application_id(tenant)
    app = db.get(LogApplicationModel, aid)
    if app is None:
        # Inactive, no externally usable key; Muse bearer is checked by router.
        try:
            with db.begin_nested():
                app = LogApplicationModel(
                    id=aid, tenant_id=tenant, slug=SLUG,
                    display_name="CAM Muse observability", key_prefix="internal",
                    secret_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                    active=False,
                )
                db.add(app)
                db.flush()
        except IntegrityError:
            app = db.get(LogApplicationModel, aid)
    if app is None or app.tenant_id != tenant or app.slug != SLUG:
        raise RuntimeError("Muse telemetry application unavailable")
    return app


def _write(db, tenant, event_type, key, payload, occurred, now, trace=None,
           level="info"):
    aid = _application(db, tenant).id
    digest = hashlib.sha256(json.dumps(
        [event_type, payload, occurred.isoformat(), trace],
        sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    def existing():
        return db.scalar(select(ApplicationLogModel).where(
            ApplicationLogModel.application_id == aid,
            ApplicationLogModel.idempotency_key == key,
        ))
    row = existing()
    if row is not None:
        return "duplicate" if row.request_hash == digest else "conflict"
    try:
        with db.begin_nested():
            db.add(ApplicationLogModel(
                tenant_id=tenant, application_id=aid,
                idempotency_key=key, request_hash=digest, level=level,
                event_type=event_type, trace_id=trace, payload_json=payload,
                occurred_at=occurred, received_at=now,
                expires_at=now + timedelta(days=RETENTION_DAYS),
            ))
            db.flush()
    except IntegrityError:
        row = existing()
        if row is None:
            raise
        return "duplicate" if row.request_hash == digest else "conflict"
    return "accepted"


def ingest(db, tenant, worker, batch, now=None):
    if worker not in WORKERS:
        raise HTTPException(403, "Unknown telemetry worker")
    now = now or utcnow()
    accepted, duplicates, rejected = [], [], []
    for event in batch.events:
        eid = str(event.eventId)
        if not now-timedelta(days=RETENTION_DAYS) <= event.occurredAtUtc <= now+timedelta(minutes=5):
            rejected.append({"eventId": eid, "code": "EVENT_TIMESTAMP_OUT_OF_RANGE"})
            continue
        payload = event.model_dump(mode="json", exclude_none=True)
        payload["workerId"] = worker  # authentication, not caller input
        payload["origin"] = "worker_reported"
        result = _write(db, tenant, event.eventType, "worker:"+worker+":"+eid,
                        payload, event.occurredAtUtc, now,
                        trace=str(event.commandId or event.runId) if (event.commandId or event.runId) else None,
                        level="warning" if event.errorCode not in (None, "NONE") else "info")
        if result == "accepted":
            accepted.append(eid)
        elif result == "duplicate":
            duplicates.append(eid)
        else:
            rejected.append({"eventId": eid, "code": "EVENT_ID_CONFLICT"})
    db.commit()
    return {"ok": not rejected, "policy": POLICY, "accepted": accepted,
            "duplicates": duplicates, "rejected": rejected}


def contract():
    return {
        "version": POLICY, "enabled": enabled(),
        "eventsPath": "/api/v1/muse-bridge/automation/events",
        "diagnosticsPath": "/api/v1/muse-bridge/automation/diagnostics",
        "runnerIntegrationPath": "/api/v1/muse-bridge/automation/runner-integration",
        "runnerIntegrationRequiresAuthorizedOwnerInstall": True,
        "workerHeartbeatSeconds": HEARTBEAT_SECONDS,
        "workerStaleSeconds": STALE_SECONDS,
        "retentionDays": RETENTION_DAYS, "maxBatchEvents": 20,
        "workerIdentityFromBearer": True, "telemetryDoesNotRenewLeases": True,
        "outboxProtocol": "Persist eventId and event JSON before send; retry unchanged on network/5xx. Remove only accepted/duplicate IDs. Quarantine rejected IDs. Never store credentials in the outbox.",
        "instructions": "Send RUN_STARTED and independent WORKER_HEARTBEAT during idle, work and cooldown; send stage outcomes and RUN_FINISHED. Use the actual provider retry-after; never fabricate progress. Keep command heartbeats and upload contracts unchanged.",
    }


# Only fixed route classes and validated IDs enter telemetry.
_ROUTE = re.compile(r"^/api/v1/muse-bridge/(commands|jobs|reviews)/([^/]+)/(claim|heartbeat|complete|yield|skip-report|artwork|verdict|story-contract|video-status|hero/(?:vertical|horizontal)|videos/(?:vertical|horizontal)|placement/preflight|submit)$")
_DISCOVERY = {"commands": "command_poll", "commands/wait": "command_poll",
              "video-work": "video_poll", "reviews": "review_poll",
              "workers/config": "worker_config", "workers/cooldown": "cooldown_report"}


def route_info(path):
    prefix = "/api/v1/muse-bridge/"
    if not path.startswith(prefix):
        return None
    suffix = path[len(prefix):]
    if suffix in _DISCOVERY:
        return {"route": _DISCOVERY[suffix]}
    match = _ROUTE.fullmatch(path)
    if not match:
        return None
    family, identifier, action = match.groups()
    if family == "commands":
        try:
            identifier = str(UUID(identifier))
        except ValueError:
            return None
        return {"route": "command_" + action, "commandId": identifier}
    if not re.fullmatch(r"[0-9]{5,20}", identifier):
        return None
    return {"route": family + "_" + action.replace("/", "_"), "listingId": identifier}


def safe_error(status, detail=None):
    if status < 400:
        return None
    # Compare only against known markers; never store the original detail.
    text = detail.lower() if isinstance(detail, str) else ""
    if status == 409:
        if "lease" in text:
            return "LEASE_REJECTED"
        if "standby" in text:
            return "WORKER_STANDBY"
        if "policy" in text:
            return "POLICY_MISMATCH"
        if "receipt" in text or "verified" in text:
            return "RECEIPT_OR_PROOF_REQUIRED"
        return "STATE_CONFLICT"
    if status == 422:
        return "INVALID_CONTRACT"
    if status == 404:
        return "RESOURCE_NOT_FOUND"
    if status == 429:
        return "RATE_LIMIT_OR_COOLDOWN"
    if status >= 500:
        return "CAM_SERVER_ERROR"
    return "CAM_REQUEST_REJECTED"


def safe_business_result(result):
    """Only enum values and bounded scalars from committed business outcomes."""
    if not isinstance(result, dict):
        return {}
    out = {}
    enums = {"outcome": {"success", "failed"}, "phase": {"create_video", "review_artwork", "execute_task"},
             "commandStatus": {"pending", "claimed", "failed", "completed", "receipt_submitted", "reviewed"},
             "jobStatus": {"pending", "qa_hold", "claimed", "submitted", "completed"},
             "qaVerdict": {"pass", "hold", "fail"},
             "skipReason": {"terminal_hold", "operator_paused", "source_blocked", "temporary_blocked", "provider_refused"}}
    for key, options in enums.items():
        value = result.get(key)
        if isinstance(value, str) and value in options:
            out[key] = value
    for key in ("retryScheduled", "capabilityHold", "skipHoldActive", "artworkOriginalVerified",
                "videoDiscoveryAllowed", "newVideoStorageVerified"):
        if type(result.get(key)) is bool:
            out[key] = result[key]
    if (out.get("artworkOriginalVerified") is True
            and type(result.get("artworkBytes")) is int
            and 57 <= result["artworkBytes"] <= 20 * 1024 * 1024):
        out["artworkBytes"] = result["artworkBytes"]
    if (type(result.get("videoCandidatesOffered")) is int
            and 0 <= result["videoCandidatesOffered"] <= 20):
        out["videoCandidatesOffered"] = result["videoCandidatesOffered"]
    if type(result.get("providerHttpStatus")) is int and result["providerHttpStatus"] in (429,502,503,504):
        out["providerHttpStatus"] = result["providerHttpStatus"]
        out["providerStatusIndependentlyVerified"] = False
    if type(result.get("retryAfterSeconds")) is int and 0 <= result["retryAfterSeconds"] <= 86400:
        out["retryAfterSeconds"] = result["retryAfterSeconds"]
    lid = result.get("listingId")
    if isinstance(lid, str) and re.fullmatch(r"[0-9]{5,20}", lid):
        out["listingId"] = lid
    return out


def observe_api(tenant, worker, info, method, status, duration_ms, correlation, detail=None, business_result=None):
    if not enabled() or worker not in WORKERS:
        return
    now = utcnow()
    route = info["route"]
    event = {**info, "workerId": worker, "origin": "cam_observed",
             "method": method, "httpStatus": status, "durationMs": round(duration_ms),
             "correlationId": correlation, "errorCode": safe_error(status, detail)}
    if status < 400:
        event.update(safe_business_result(business_result))
        if event.get("outcome") == "failed":
            event["errorCode"] = ("RENDERER_CAPABILITY_HOLD" if event.get("capabilityHold") else
                                  "PROVIDER_UNAVAILABLE" if event.get("providerHttpStatus") else "COMMAND_FAILED")
        elif event.get("qaVerdict") in ("hold", "fail"):
            event["errorCode"] = "QA_HOLD"
    # Aggregate frequent successful poll/heartbeat rows by minute. Errors and
    # mutating steps remain independently traceable.
    sampled = status < 400 and route in {"command_poll", "video_poll", "review_poll",
                                         "worker_config", "command_heartbeat"}
    key = ("cam:"+worker+":"+route+":"+info.get("commandId","")+":"+str(int(now.timestamp())//60)
           if sampled else "cam:"+correlation)
    if not API_WRITE_SLOTS.acquire(blocking=False):
        LOGGER.warning("Muse telemetry busy route=%s", route)
        return
    try:
        with SessionLocal() as db:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text("SET LOCAL statement_timeout = '750ms'"))
            _write(db, tenant, "CAM_API_RESULT", key, event, now, now,
                   info.get("commandId") or correlation,
                   level="warning" if event.get("errorCode") else "info")
            db.commit()
    except Exception:
        # Do not fail a successful business request because telemetry failed.
        LOGGER.warning("Muse telemetry write failed route=%s status=%s", route, status)
    finally:
        API_WRITE_SLOTS.release()


def observe_recovery(tenant, report=None, failed=False):
    if not enabled() or not tenant:
        return
    now = utcnow()
    allowed = ("closed", "expiredCommandsRecovered", "expiredJobsReleased",
               "newVideoCommands", "newReviewCommands", "eligibleInflight",
               "eligibleInflightAfter", "reviewInflightAfter", "videoInflightAfter",
               "transitionalOverflow", "cap", "partialQaMetadataRepaired", "heldPendingListings", "sourceScopeRechecksQueued", "readyVideoListings", "activeVideoListings",
               "artworkVersionsAdvanced", "artworkVersionsRestored", "artworkVersionsDeferred",
               "artworkVersionsHeld", "artworkVersionsAwaitingOriginal", "skipped")
    payload = {k: report[k] for k in allowed if isinstance(report,dict) and k in report
               and isinstance(report[k], (int,bool))}
    payload.update({"origin":"cam_observed", "failed":failed})
    try:
        with SessionLocal() as db:
            _write(db, tenant, "CAM_RECOVERY_FAILED" if failed else "CAM_RECOVERY_SWEEP",
                   "recovery:"+str(uuid4()), payload, now, now,
                   level="error" if failed else "info")
            db.execute(delete(ApplicationLogModel).where(
                ApplicationLogModel.application_id == application_id(tenant),
                ApplicationLogModel.expires_at <= now))
            db.commit()
    except Exception:
        LOGGER.warning("Muse recovery telemetry write failed")


def _rows(db, tenant, now, *filters):
    return select(ApplicationLogModel).where(
        ApplicationLogModel.tenant_id == tenant,
        ApplicationLogModel.application_id == application_id(tenant),
        ApplicationLogModel.expires_at > now, *filters)


def _view(row):
    return {"eventType":row.event_type, "occurredAtUtc":aware(row.occurred_at).isoformat(),
            "receivedAtUtc":aware(row.received_at).isoformat(),
            "traceId":row.trace_id, "payload":row.payload_json}


def timeline(db, tenant, now, limit=100, command_id=None, worker=None):
    filters = [ApplicationLogModel.event_type != "CAM_WATCHDOG_STATE"]
    if command_id:
        filters.append(ApplicationLogModel.trace_id == command_id)
    if worker:
        filters.append(ApplicationLogModel.payload_json["workerId"].as_string() == worker)
    rows = db.scalars(_rows(db,tenant,now,*filters).order_by(
        ApplicationLogModel.received_at.desc(),ApplicationLogModel.id.desc()).limit(limit)).all()
    return [_view(r) for r in rows]


def _last(db, tenant, now, *filters, order_by=None):
    return db.scalar(_rows(db,tenant,now,*filters).order_by(
        order_by if order_by is not None else ApplicationLogModel.received_at.desc()).limit(1))


def _age(row, now, field="received_at"):
    return max(0, int((now-aware(getattr(row,field))).total_seconds())) if row else None


def diagnostics(db, tenant, *, recovery_enabled, worker_ids, now=None):
    now = now or utcnow()
    workers = []
    for worker in worker_ids:
        f = ApplicationLogModel.payload_json["workerId"].as_string() == worker
        reported = _last(db,tenant,now,f,ApplicationLogModel.event_type=="WORKER_HEARTBEAT",
                         order_by=ApplicationLogModel.occurred_at.desc())
        observed = _last(db,tenant,now,f,ApplicationLogModel.event_type=="CAM_API_RESULT",
                         ApplicationLogModel.payload_json["httpStatus"].as_integer()<400)
        operational = _last(db,tenant,now,f,ApplicationLogModel.event_type=="CAM_API_RESULT",
                            ApplicationLogModel.payload_json["httpStatus"].as_integer()<400,
                            ApplicationLogModel.payload_json["route"].as_string()!="worker_config")
        config_signal = _last(db,tenant,now,f,ApplicationLogModel.event_type=="CAM_API_RESULT",
                              ApplicationLogModel.payload_json["httpStatus"].as_integer()<400,
                              ApplicationLogModel.payload_json["route"].as_string()=="worker_config")
        error = _last(db,tenant,now,f,ApplicationLogModel.level.in_(("warning","error")))
        # Late replay cannot fake an online worker. Require actual event time.
        age = _age(reported,now,"occurred_at")
        api_age = _age(observed,now)
        operational_age = _age(operational,now)
        config_age = _age(config_signal,now)
        signal_kind = ("operational" if operational_age is not None and operational_age<=STALE_SECONDS else
                       "config_only" if config_age is not None and config_age<=STALE_SECONDS else "none")
        if age is not None and age <= STALE_SECONDS:
            status = "HEARTBEAT_RECENT"
        elif api_age is not None and api_age <= STALE_SECONDS:
            status = "API_ACTIVE_HEARTBEAT_UNCONFIRMED"
        elif reported or observed:
            status = "NO_RECENT_SIGNAL"
        else:
            status = "NOT_OBSERVED"
        workers.append({"workerId":worker,"status":status,
                        "heartbeatAgeSeconds":age,"apiActivityAgeSeconds":api_age,
                        "apiSignalKind":signal_kind,"operationalApiActivityAgeSeconds":operational_age,
                        "configActivityAgeSeconds":config_age,
                        "lastOperationalApiActivity":_view(operational) if operational else None,
                        "lastHeartbeat":_view(reported) if reported else None,
                        "lastApiActivity":_view(observed) if observed else None,
                        "lastError":_view(error) if error else None})
    sweep = _last(db,tenant,now,ApplicationLogModel.event_type=="CAM_RECOVERY_SWEEP")
    fail = _last(db,tenant,now,ApplicationLogModel.event_type=="CAM_RECOVERY_FAILED")
    sweep_age = _age(sweep,now)
    failed_after_success = fail is not None and (sweep is None or aware(fail.received_at)>aware(sweep.received_at))
    recovery_status = ("DISABLED" if not recovery_enabled else "LAST_SWEEP_FAILED" if failed_after_success
                       else "RECENT" if sweep_age is not None and sweep_age<=RECOVERY_STALE_SECONDS
                       else "STALE" if sweep else "NOT_OBSERVED")
    def counts(model):
        rows = db.execute(select(model.status,func.count()).where(
            model.tenant_id==tenant).group_by(model.status)).all()
        def count(*conditions):
            return int(db.scalar(select(func.count()).select_from(model).where(
                model.tenant_id==tenant,*conditions)) or 0)
        result={"byStatus":dict(rows),
                "expiredClaims":count(model.status=="claimed",model.lease_expires_at<=now),
                "missingLeaseExpiry":count(model.status=="claimed",model.lease_expires_at.is_(None))}
        if model is CamMuseCommand:
            expired=(model.status=="claimed",model.lease_expires_at<=now)
            result["genericExpiredClaims"]=count(*expired,model.action=="execute_task")
            result["garmentExpiredClaims"]=count(*expired,model.action!="execute_task")
        return result
    return {"ok":True,"policy":POLICY,"asOfUtc":now.isoformat(),
            "workers":workers,"queueRecovery":{"enabled":recovery_enabled,
                "status":recovery_status,"lastSweepAgeSeconds":sweep_age,
                "lastSweep":_view(sweep) if sweep else None,
                "lastFailure":_view(fail) if fail else None},
            "commands":counts(CamMuseCommand),"jobs":counts(CamMuseJob),
            "recentEvents":timeline(db,tenant,now,limit=30)}