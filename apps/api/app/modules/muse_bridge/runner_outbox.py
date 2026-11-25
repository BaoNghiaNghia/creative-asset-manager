"""Portable telemetry outbox for the EXISTING Muse runner; no job operations.
Transport is injected: send({"events":[...]}) -> (HTTP status, decoded JSON, headers).
Use the existing authenticated CAM HTTP helper. Do not write credentials here.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID, uuid4

TYPES = set("RUN_STARTED WORKER_HEARTBEAT POLL_SUCCESS QUEUE_EMPTY COMMAND_CLAIMED JOB_CLAIMED HERO_READY RENDER_STARTED RENDER_SUCCEEDED RENDER_FAILED COOLDOWN_STARTED COOLDOWN_ENDED VIDEO_UPLOADED CAM_ACK_RECEIVED RUN_FINISHED LOG_REPLAYED WORKER_ERROR".split())
STATES = set("idle polling reviewing rendering uploading cooldown blocked stopping".split())
ERRORS = set("NONE NETWORK_ERROR TIMEOUT PROVIDER_RATE_LIMIT VIDEO_DAILY_BUDGET_EXHAUSTED PROVIDER_UNAVAILABLE LEASE_REJECTED INVALID_CONTRACT ARTWORK_UNAVAILABLE QA_HOLD GARMENT_MISMATCH UPLOAD_FAILED CAM_ACK_FAILED RENDERER_UNAVAILABLE INTERNAL_ERROR UNKNOWN".split())

class Outbox:
    def __init__(self, directory, send):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.quarantine = self.directory / "rejected"
        self.quarantine.mkdir(mode=0o700, exist_ok=True)
        self.send = send
        self.run_id = str(uuid4())
        self.lock = threading.Lock()
        self.flush_lock = threading.Lock()
        self.retry_at = 0.0
        self.last_heartbeat = float("-inf")

    def emit(self, kind, *, state=None, command_id=None, listing_id=None,
             aspect=None, error=None, http=None, retry_after=None, duration=None,
             render_attempt_id=None):
        if kind not in TYPES or (state is not None and state not in STATES):
            raise ValueError("invalid telemetry type/state")
        if error is not None and error not in ERRORS:
            raise ValueError("invalid telemetry error")
        e = {"eventId":str(uuid4()),"eventType":kind,"runId":self.run_id,
             "occurredAtUtc":datetime.now(timezone.utc).isoformat()}
        if render_attempt_id is not None:
            e["renderAttemptId"] = str(UUID(str(render_attempt_id)))
        if command_id is not None:
            e["commandId"] = str(UUID(str(command_id)))
        if listing_id is not None:
            value = str(listing_id)
            if not value.isascii() or not value.isdigit() or not 5<=len(value)<=20:
                raise ValueError("invalid listing ID")
            e["listingId"] = value
        if aspect is not None:
            if aspect not in ("9:16","16:9"):
                raise ValueError("invalid aspect")
            e["aspectRatio"] = aspect
        for key,value,low,high in (("httpStatus",http,100,599),("retryAfterSeconds",retry_after,0,86400),("durationMs",duration,0,86400000)):
            if value is not None:
                if type(value) is not int or not low<=value<=high:
                    raise ValueError("invalid telemetry integer")
                e[key] = value
        if state is not None: e["state"] = state
        if error is not None: e["errorCode"] = error
        with self.lock:
            # Never silently delete pending event records on overflow.
            if sum(1 for _ in self.directory.glob("*.json")) >= 10000:
                raise OSError("telemetry outbox full")
            path=self.directory / (e["eventId"]+".json")
            temporary=path.with_suffix(".tmp")
            with open(temporary,"x",encoding="utf-8") as f:
                if os.name != "nt": os.chmod(temporary,0o600)
                json.dump(e,f,separators=(",",":"))
                f.flush(); os.fsync(f.fileno())
            os.replace(temporary,path)
        return e["eventId"]

    def heartbeat(self, state, **context):
        # Call from actual runner activity, including real idle/cooldown checks.
        # Never run a stand-alone heartbeat process that outlives the runner.
        now=time.monotonic()
        if now-self.last_heartbeat<60: return None
        eid=self.emit("WORKER_HEARTBEAT",state=state,**context)
        self.last_heartbeat=now
        return eid

    def flush(self):
        if time.monotonic()<self.retry_at or not self.flush_lock.acquire(False):
            return {"deferred":True}
        try:
            with self.lock:
                paths=sorted(self.directory.glob("*.json"),key=lambda p:p.stat().st_mtime)
                # Bounded batch, preserve event IDs/timestamps across attempts.
                events=[]
                for path in paths[:20]:
                    try:
                        event=json.loads(path.read_text(encoding="utf-8"))
                        stamp=datetime.fromisoformat(event["occurredAtUtc"])
                        if stamp<datetime.now(timezone.utc)-timedelta(days=7):
                            os.replace(path,self.quarantine/path.name)
                            continue
                        if path.stem!=str(UUID(event["eventId"])): raise ValueError()
                        events.append(event)
                    except (ValueError,KeyError,TypeError):
                        os.replace(path,self.quarantine/path.name)
            if not events: return {"empty":True}
            try:
                status,body,headers=self.send({"events":events})
            except Exception:
                self.retry_at=time.monotonic()+30
                return {"retry":True}
            if status!=200 or not isinstance(body,dict):
                try: delay=max(5,min(86400,int(headers.get("Retry-After",30))))
                except (AttributeError,ValueError,TypeError): delay=30
                self.retry_at=time.monotonic()+delay
                return {"retry":True,"httpStatus":status}
            allowed={e["eventId"] for e in events}
            def ids(key):
                value=body.get(key)
                return {i for i in value if isinstance(i,str) and i in allowed} if isinstance(value,list) else set()
            ack=ids("accepted")|ids("duplicates")
            rejected=body.get("rejected",[])
            rejected={r.get("eventId") for r in rejected if isinstance(r,dict) and isinstance(r.get("eventId"),str) and r["eventId"] in allowed} if isinstance(rejected,list) else set()
            ack-=rejected
            with self.lock:
                for eid in ack:
                    (self.directory/(eid+".json")).unlink(missing_ok=True)
                for eid in rejected:
                    p=self.directory/(eid+".json")
                    if p.exists(): os.replace(p,self.quarantine/p.name)
            if allowed-ack-rejected:
                self.retry_at=time.monotonic()+30
            return {"acknowledged":len(ack),"rejected":len(rejected),"unacknowledged":len(allowed-ack-rejected)}
        finally:
            self.flush_lock.release()