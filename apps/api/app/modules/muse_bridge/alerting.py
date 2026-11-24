"""Independent CAM-side incident watchdog. No renderer, command or job mutations.

State is a bounded private record in existing application_logs; Telegram is
opt-in via dedicated server environment variables. Never store credentials.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
from datetime import timedelta
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError
from uuid import uuid4

from sqlalchemy import select, text
from app.core.database import SessionLocal
from app.modules.application_logs.model import ApplicationLogModel
from . import observability as obs

POLICY = "CAM_MUSE_WATCHDOG_V1"
STATE_KEY = "cam:watchdog-state:v1"
STATE_TYPE = "CAM_WATCHDOG_STATE"
INTERVAL = 120
CONFIRMATIONS = 2
MIN_SEND_GAP = 300
LOGGER = logging.getLogger("uvicorn.error")


def enabled():
    return obs.enabled() and os.getenv("CAM_MUSE_WATCHDOG_ENABLED", "true").lower().strip() == "true" and bool(os.getenv("CAM_MUSE_TENANT_ID"))


def telegram_config():
    if os.getenv("CAM_MUSE_TELEGRAM_ENABLED", "false").lower().strip() != "true":
        return None
    token = os.getenv("CAM_MUSE_TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("CAM_MUSE_TELEGRAM_CHAT_ID", "")
    if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", token):
        return None
    if not re.fullmatch(r"-?[0-9]{3,24}", chat):
        return None
    return token, chat


def lock(db, tenant):
    if db.get_bind().dialect.name != "postgresql":
        return True
    key = int.from_bytes(hashlib.sha256((POLICY+":"+tenant).encode()).digest()[:8],
                         "big", signed=True)
    return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key":key}))


def state_row(db, tenant, now, create=False):
    row = db.scalar(select(ApplicationLogModel).where(
        ApplicationLogModel.tenant_id==tenant,
        ApplicationLogModel.application_id==obs.application_id(tenant),
        ApplicationLogModel.idempotency_key==STATE_KEY))
    if row is None and create:
        aid = obs._application(db,tenant).id
        row = ApplicationLogModel(tenant_id=tenant, application_id=aid,
            idempotency_key=STATE_KEY, request_hash=hashlib.sha256(STATE_KEY.encode()).hexdigest(),
            level="info", event_type=STATE_TYPE, payload_json={},
            occurred_at=now,received_at=now,expires_at=now+timedelta(days=obs.RETENTION_DAYS))
        db.add(row)
        db.flush()
    return row


def save(row, state, now):
    row.payload_json = state
    row.received_at = now
    row.expires_at = now+timedelta(days=obs.RETENTION_DAYS)


def evaluate(state, alerts, now):
    """Debounce both opening and resolving an incident. Stable IDs, not raw notes.

    Routine cooldown, standby, one-off recent errors, and expired unrelated
    generic tasks remain visible in UI but do not trigger push alerts.
    """
    state = dict(state or {})
    candidates = {}
    old = state.get("candidates") or {}
    active = dict(state.get("active") or {})
    prior = dict(active)
    eligible = {}
    for a in alerts:
        aid = a.get("id","")
        if (a.get("severity") not in ("warning","error") or
            not re.fullmatch(r"[a-z0-9:-]{1,64}",aid) or
            aid.endswith(":recent-error") or aid.startswith("generic:")):
            continue
        eligible[aid] = {"severity":a["severity"],"message":str(a.get("message",""))[:240]}
    for aid in sorted(set(old)|set(active)|set(eligible))[:40]:
        previous = old.get(aid) or {}
        if aid in eligible:
            a = eligible[aid]
            seen = min(CONFIRMATIONS, int(previous.get("seen",0))+1) if previous.get("severity")==a["severity"] else 1
            candidates[aid] = {**a,"seen":seen,"missed":0}
            if seen>=CONFIRMATIONS:
                active[aid]=a
        elif aid in active:
            missed=min(CONFIRMATIONS,int(previous.get("missed",0))+1)
            if missed>=CONFIRMATIONS:
                active.pop(aid,None)
            else:
                candidates[aid]={**active[aid],"seen":CONFIRMATIONS,"missed":missed}
    state.update({"policy":POLICY,"lastSweepUtc":obs.aware(now).isoformat(),
                  "candidates":candidates,"active":active})
    opened=[aid for aid in active if aid not in prior or prior[aid]["severity"]!=active[aid]["severity"]]
    resolved=[aid for aid in prior if aid not in active]
    return state, sorted(opened), sorted(resolved)


def fingerprint(active):
    return hashlib.sha256(json.dumps(sorted((k,v["severity"]) for k,v in active.items()),
                                     separators=(",",":")).encode()).hexdigest()


def reserve_delivery(state, now, configured):
    """Reserve one grouped snapshot before sending; no secret in state."""
    if not configured:
        return None
    active=state.get("active") or {}
    delivery=dict(state.get("delivery") or {})
    target=fingerprint(active)
    previous=delivery.get("sentFingerprint")
    if previous==target or (not active and not previous):
        return None
    ts=int(now.timestamp())
    if ts<int(delivery.get("nextAttemptEpoch",0)):
        return None
    attempt=str(uuid4())
    delivery.update({"attemptId":attempt,"status":"sending","nextAttemptEpoch":ts+MIN_SEND_GAP,
                     "lastAttemptUtc":obs.aware(now).isoformat()})
    state["delivery"]=delivery
    resolved=sorted(set(delivery.get("sentIds") or [])-set(active))
    lines=["CAM Muse watchdog", "Active incidents: "+str(len(active))]
    for aid,a in sorted(active.items()):
        lines.append(a["severity"].upper()+": "+a["message"])
    if resolved:
        lines.append("Resolved: "+", ".join(resolved))
    lines.append("Open CAM > Etsy Spy > Muse Monitor for details.")
    return {"id":attempt,"fingerprint":target,"ids":sorted(active),"message":"\n".join(lines)[:3500]}


def finish_delivery(state, reservation, now, success, delay=0):
    delivery=dict(state.get("delivery") or {})
    if delivery.get("attemptId")!=reservation["id"]:
        return False
    delivery["status"]="sent" if success else "retry_pending"
    delivery["lastResultUtc"]=obs.aware(now).isoformat()
    if success:
        delivery.update({"sentFingerprint":reservation["fingerprint"],"sentIds":reservation["ids"],
                         "failures":0,"lastSuccessUtc":obs.aware(now).isoformat()})
    else:
        delivery["failures"]=min(8,int(delivery.get("failures",0))+1)
    seconds=MIN_SEND_GAP if success else max(MIN_SEND_GAP,min(3600,MIN_SEND_GAP*2**min(3,delivery["failures"]-1)),min(86400,max(0,delay)))
    delivery["nextAttemptEpoch"]=int(now.timestamp())+seconds
    state["delivery"]=delivery
    return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send_telegram(config, message):
    """One fixed HTTPS destination; no proxies, redirects, raw response logs."""
    token,chat=config
    opener=build_opener(ProxyHandler({}),NoRedirect())
    body=json.dumps({"chat_id":chat,"text":message,"disable_web_page_preview":True}).encode()
    request=Request("https://api.telegram.org/bot"+token+"/sendMessage",
                    data=body,headers={"Content-Type":"application/json"},method="POST")
    try:
        with opener.open(request,timeout=8) as response:
            return response.status==200 and json.loads(response.read(65536)).get("ok") is True,0
    except HTTPError as exc:
        try:
            retry=int(exc.headers.get("Retry-After","0"))
        except (TypeError,ValueError):
            retry=0
        return False,retry
    except Exception:
        return False,0


def summary(db, tenant, now=None):
    now=now or obs.utcnow()
    row=state_row(db,tenant,now)
    state=(row.payload_json or {}) if row else {}
    delivery=state.get("delivery") or {}
    age=int((now-obs.aware(row.received_at)).total_seconds()) if row else None
    configured=telegram_config() is not None
    requested=os.getenv("CAM_MUSE_TELEGRAM_ENABLED","false").lower().strip()=="true"
    return {"policy":POLICY,"enabled":enabled(),"lastSweepAgeSeconds":age,
            "status":"DISABLED" if not enabled() else "RECENT" if age is not None and age<=360 else "STALE" if row else "NOT_OBSERVED",
            "activeIncidentIds":sorted((state.get("active") or {}).keys()),
            "telegram":{"configured":configured,
                "status":delivery.get("status","ready") if configured else "invalid_config" if requested else "not_configured",
                "lastSuccessUtc":delivery.get("lastSuccessUtc") if configured else None}}


def run_configured_watchdog():
    if not enabled():
        return None
    from fastapi import Response
    from . import router as muse
    from .monitor import derive_alerts
    _,tenant=muse._configured()
    now=obs.utcnow()
    config=telegram_config()
    with SessionLocal() as db:
        if not lock(db,tenant):
            return {"skipped":True}
        report=muse.muse_automation_diagnostics(Response(),tenant=tenant,db=db)
        row=state_row(db,tenant,now,create=True)
        state,opened,resolved=evaluate(row.payload_json,derive_alerts(report,now),now)
        if opened or resolved:
            obs._write(db,tenant,"CAM_WATCHDOG_TRANSITION","watchdog:"+str(uuid4()),
                       {"origin":"cam_observed","opened":opened,"resolved":resolved,
                        "activeIncidentIds":sorted(state["active"])},now,now,
                       level="warning" if opened else "info")
        reservation=reserve_delivery(state,now,config is not None)
        save(row,state,now)
        db.commit()
    if reservation is not None:
        ok,delay=send_telegram(config,reservation["message"])
        stamp=obs.utcnow()
        with SessionLocal() as db:
            if lock(db,tenant):
                row=state_row(db,tenant,stamp)
                if row is not None:
                    state=dict(row.payload_json or {})
                    if finish_delivery(state,reservation,stamp,ok,delay):
                        save(row,state,stamp)
                        obs._write(db,tenant,"CAM_WATCHDOG_DELIVERY","watchdog:delivery:"+reservation["id"],
                            {"origin":"cam_observed","delivery":"sent" if ok else "retry_pending"},
                            stamp,stamp,level="info" if ok else "warning")
                        db.commit()
    return {"skipped":False,"opened":opened,"resolved":resolved,"active":len(state["active"]),
            "telegramConfigured":config is not None}