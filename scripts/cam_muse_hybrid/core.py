"""CAM Muse Hybrid V1: portable, opt-in integration core.

No browser launch, credentials, CAM writes, provider calls or scheduler on
import. Live operations require the existing authorized runner's transport,
ownership/lease checks, hero and video QA, receipt verifier, and a verified
renderer. This journal tracks technical render intents, not CAM business work.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
from typing import Callable
from uuid import UUID

POLICY = "CAM_MUSE_HYBRID_GATEWAY_V1"
SHA = re.compile(r"^[a-f0-9]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
TASK = re.compile(r"^task_[a-f0-9]{24}$")
PNG = b"\x89PNG\r\n\x1a\n"
ACTIVE = {"prepared", "submitting", "unknown", "submitted", "rendering",
          "result_ready", "partial", "awaiting_receipt"}
FINAL = {"verified", "terminal_rejected", "superseded"}
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
DISCOVERY_PATH = "/api/v1/muse-bridge/commands/wait?limit=20&waitSeconds=20"


class HybridBlocked(RuntimeError):
    """Safe, nonsecret reason code. Never used to release a CAM lease."""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8"))


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def checked_label(value, code):
    if not isinstance(value,str) or not LABEL.fullmatch(value):
        raise HybridBlocked(code)
    return value


def checked_sha(value, code):
    if not isinstance(value,str) or not SHA.fullmatch(value):
        raise HybridBlocked(code)
    return value


@dataclass(frozen=True)
class RenderIdentity:
    tenant: str
    listing_id: str
    artwork_sha256: str
    garment: str
    ratio: str
    story_version: str
    take_index: int
    generation_revision: str

    def __post_init__(self):
        checked_label(self.tenant,"INVALID_TENANT")
        if not re.fullmatch(r"[0-9]{5,20}", self.listing_id):
            raise HybridBlocked("INVALID_LISTING")
        checked_sha(self.artwork_sha256,"INVALID_ARTWORK_SHA")
        if self.garment != "C1717" or self.ratio not in ("9:16","16:9"):
            raise HybridBlocked("INVALID_GARMENT_OR_RATIO")
        checked_label(self.story_version,"INVALID_STORY")
        checked_label(self.generation_revision,"INVALID_GENERATION_REVISION")
        if type(self.take_index) is not int or not 1 <= self.take_index <= 20:
            raise HybridBlocked("INVALID_TAKE")

    @property
    def key(self):
        return fingerprint({"policy":POLICY,**asdict(self)})


@dataclass(frozen=True)
class RenderIntent:
    identity: RenderIdentity
    worker: str
    command_id: str
    contract_sha256: str
    hero_sha256: str
    duration_seconds: int
    backend_id: str

    def __post_init__(self):
        checked_label(self.worker,"INVALID_WORKER")
        try:
            if str(UUID(self.command_id)) != self.command_id:
                raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise HybridBlocked("INVALID_COMMAND_ID") from None
        checked_sha(self.contract_sha256,"INVALID_CONTRACT_SHA")
        checked_sha(self.hero_sha256,"INVALID_HERO_SHA")
        checked_label(self.backend_id,"INVALID_BACKEND")
        if type(self.duration_seconds) is not int or not 1 <= self.duration_seconds <= 60:
            raise HybridBlocked("INVALID_DURATION")


class Journal:
    """Single-owner OS lock and atomic JSON. No extra database service.

    Hold this context for the actual worker lifetime. An OS lock is released
    when the process exits; a stale lock file is NOT authority to take a CAM
    lease. Use a private owner-only directory/ACL on Windows. No raw prompt,
    media, secret, cookie, authorization or lease token belongs in this file.
    """
    def __init__(self, directory):
        self.root = Path(directory)
        self.path = self.root / "render-intents.json"
        self.lock_file = None
        self.records = {}
        self.poisoned = False
        self.mutex = threading.RLock()

    def __enter__(self):
        if self.lock_file is not None:
            raise HybridBlocked("JOURNAL_ALREADY_OPEN")
        self.poisoned=False
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.lock_file = open(self.root/"worker.lock","a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.lock_file.seek(0,2)
                if self.lock_file.tell() == 0:
                    self.lock_file.write(b"0"); self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close(); self.lock_file=None
            raise HybridBlocked("WORKER_ALREADY_RUNNING") from None
        try:
            self._load()
        except Exception:
            self.__exit__(None,None,None)
            raise
        return self

    def __exit__(self,*_):
        if self.lock_file:
            self.lock_file.close()
            self.lock_file=None

    def _load(self):
        if not self.path.exists():
            self.records={}
            return
        try:
            if self.path.stat().st_size > MAX_JOURNAL_BYTES:
                raise ValueError()
            obj=json.loads(self.path.read_text(encoding="utf-8"))
            if obj["policy"] != POLICY or not isinstance(obj["records"],dict):
                raise ValueError()
            records=obj["records"]
            for key,record in records.items():
                intent=record["intent"]
                identity=RenderIdentity(**intent["identity"])
                RenderIntent(identity=identity,**{k:v for k,v in intent.items() if k!="identity"})
                checked_sha(record.get("request_sha256"),"INVALID_REQUEST_SHA")
                if key!=identity.key or record["state"] not in ACTIVE|FINAL:
                    raise ValueError()
                if record.get("task_id") is not None and not TASK.fullmatch(record["task_id"]):
                    raise ValueError()
                if record["state"] in ("submitted","rendering","result_ready","partial","awaiting_receipt","verified") and not record.get("task_id"):
                    raise ValueError()
                if record["state"] in ("awaiting_receipt","verified"):
                    checked_sha(record.get("media_sha256"),"INVALID_MEDIA_SHA")
                if record["state"]=="verified":
                    checked_label(record.get("receipt_id"),"INVALID_RECEIPT")
            if sum(r["state"] in ACTIVE for r in records.values())>1:
                raise ValueError()
        except (OSError,ValueError,KeyError,TypeError,HybridBlocked):
            raise HybridBlocked("JOURNAL_REQUIRES_RECONCILIATION") from None
        self.records=records

    def _check_open(self):
        if not self.lock_file:
            raise HybridBlocked("JOURNAL_NOT_LOCKED")
        if self.poisoned:
            raise HybridBlocked("JOURNAL_WRITE_UNCERTAIN_REOPEN_AND_RECONCILE")

    def _save(self,records):
        self._check_open()
        data=json.dumps({"policy":POLICY,"records":records},sort_keys=True,
                        separators=(",",":"),allow_nan=False).encode()
        if len(data)>MAX_JOURNAL_BYTES:
            raise HybridBlocked("JOURNAL_FULL_REQUIRES_ARCHIVE")
        tmp=self.root/(".journal-"+secrets.token_hex(8)+".tmp")
        try:
            fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,"wb") as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,self.path)
            if os.name!="nt":
                dfd=os.open(self.root,os.O_RDONLY)
                try: os.fsync(dfd)
                finally: os.close(dfd)
        except OSError:
            # A rename can succeed before a subsequent fsync fails. Do not
            # let stale in-memory state authorize a second renderer POST.
            self.poisoned=True
            raise HybridBlocked("JOURNAL_WRITE_UNCERTAIN_REOPEN_AND_RECONCILE") from None
        finally:
            tmp.unlink(missing_ok=True)
        self.records=records

    def active(self):
        with self.mutex:
            self._check_open()
            return next(((k,deepcopy(r)) for k,r in self.records.items() if r["state"] in ACTIVE),None)

    def prepare(self,intent,request_sha256):
        key=intent.identity.key
        checked_sha(request_sha256,"INVALID_REQUEST_SHA")
        with self.mutex:
            self._check_open()
            record=self.records.get(key)
            if record:
                # Coalesce the logical request, never silently replace
                # command ownership, contract or reference-image identity.
                if record["intent"]!=asdict(intent) or record.get("request_sha256")!=request_sha256:
                    raise HybridBlocked("KEY_ALREADY_BOUND_RECONCILE")
                return deepcopy(record)
            if self.active():
                raise HybridBlocked("UNRESOLVED_RENDER_EXISTS")
            record={"intent":asdict(intent),"state":"prepared",
                    "request_sha256":request_sha256,"task_id":None,"updated_at":now_utc()}
            self._save({**self.records,key:record})
            return deepcopy(record)

    def transition(self,key,expected,state,**evidence):
        allowed={"task_id","media_sha256","receipt_id","failure_code","http_status","retry_after_seconds"}
        if state not in ACTIVE|FINAL or set(evidence)-allowed:
            raise HybridBlocked("INVALID_TRANSITION")
        if "task_id" in evidence and not TASK.fullmatch(str(evidence["task_id"])):
            raise HybridBlocked("INVALID_TASK_ID")
        if "media_sha256" in evidence:
            checked_sha(evidence["media_sha256"],"INVALID_MEDIA_SHA")
        for field in ("receipt_id","failure_code"):
            if field in evidence: checked_label(evidence[field],"INVALID_EVIDENCE")
        if "http_status" in evidence and (type(evidence["http_status"]) is not int or not 100<=evidence["http_status"]<=599):
            raise HybridBlocked("INVALID_HTTP_STATUS")
        if "retry_after_seconds" in evidence and (type(evidence["retry_after_seconds"]) is not int or evidence["retry_after_seconds"]<0):
            raise HybridBlocked("INVALID_RETRY_AFTER")
        with self.mutex:
            self._check_open()
            old=self.records.get(key)
            if old is None or old["state"] not in expected:
                raise HybridBlocked("STATE_CHANGED_RECONCILE")
            if old.get("task_id") and evidence.get("task_id",old["task_id"])!=old["task_id"]:
                raise HybridBlocked("TASK_ID_CHANGED")
            updated={**old,**evidence,"state":state,"updated_at":now_utc()}
            self._save({**self.records,key:updated})
            return deepcopy(updated)


class GatewayAdapter:
    """Adapter for a locally authorized, compatibility-tested Task API.

    request(method,path,body) is an injected, bounded authenticated transport.
    It returns (status, JSON, headers), must not follow arbitrary redirects,
    and must connect to a pinned local endpoint. No account switching.
    This class does not authorize the provider or automatically retry POST.
    """
    def __init__(self,request,*,model,compatibility_verified=False):
        self.request=request
        self.model=checked_label(model,"INVALID_RENDERER_MODEL")
        self.compatibility_verified=compatibility_verified

    def build_request(self,intent,prompt,hero_png):
        if not self.compatibility_verified:
            raise HybridBlocked("RENDERER_COMPATIBILITY_UNVERIFIED")
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>20000:
            raise HybridBlocked("INVALID_RENDER_PROMPT")
        if not isinstance(hero_png,bytes) or not hero_png.startswith(PNG) or len(hero_png)>20*1024*1024:
            raise HybridBlocked("INVALID_HERO_REFERENCE")
        # The pinned imageRefs implementation accepts image data URLs. Bind
        # the actual checked bytes, rather than a potentially mutable URL.
        reference="data:image/png;base64,"+base64.b64encode(hero_png).decode("ascii")
        return {"model":self.model,"prompt":prompt,"images":[reference],
                "ratio":intent.identity.ratio,"duration":intent.duration_seconds,
                "new_chat":True,"idempotency_key":intent.identity.key}

    def submit(self,payload):
        if not self.compatibility_verified:
            raise HybridBlocked("RENDERER_COMPATIBILITY_UNVERIFIED")
        return self.request("POST","/v1/tasks",payload)

    def status(self,task_id):
        if not self.compatibility_verified:
            raise HybridBlocked("RENDERER_COMPATIBILITY_UNVERIFIED")
        if not TASK.fullmatch(task_id):
            raise HybridBlocked("INVALID_TASK_ID")
        return self.request("GET","/v1/tasks/"+task_id,None)


class Coordinator:
    """Technical task lifecycle, not a new queue.

    The trusted existing CAM runner supplies assert_ready(intent), which must
    check both actual owned leases, current artwork SHA, CAM policy, output QA
    scope and actual provider readiness before a real submit. It must raise
    if any of those checks fails. Never fabricate such a callback in live use.
    """
    def __init__(self,journal,gateway,assert_ready:Callable):
        self.journal=journal
        self.gateway=gateway
        self.assert_ready=assert_ready
        self.lock=threading.Lock()

    def submit(self,intent,*,original_png,hero_png,hero_artwork_sha,prompt):
        with self.lock:
            if not self.gateway.compatibility_verified:
                raise HybridBlocked("RENDERER_COMPATIBILITY_UNVERIFIED")
            if (not isinstance(original_png,bytes) or not original_png.startswith(PNG) or
                    digest(original_png)!=intent.identity.artwork_sha256):
                raise HybridBlocked("ARTWORK_UNAVAILABLE")
            if (not isinstance(hero_png,bytes) or not hero_png.startswith(PNG) or digest(hero_png)!=intent.hero_sha256
                    or hero_artwork_sha!=intent.identity.artwork_sha256
                    or digest(hero_png)==digest(original_png)):
                raise HybridBlocked("HERO_BINDING_INVALID")
            if self.assert_ready(intent) is not True:
                raise HybridBlocked("CAM_READINESS_NOT_VERIFIED")
            key=intent.identity.key
            payload=self.gateway.build_request(intent,prompt,hero_png)
            record=self.journal.prepare(intent,fingerprint(payload))
            if record["state"]!="prepared":
                return record
            # Commit the possibly-sent state BEFORE the one and only POST.
            # A crash after this boundary always requires reconciliation.
            self.journal.transition(key,{"prepared"},"submitting")
            try:
                status,body,headers=self.gateway.submit(payload)
            except Exception:
                return self.journal.transition(key,{"submitting"},"unknown",
                                               failure_code="SUBMIT_RESPONSE_UNKNOWN")
            if type(status) is not int or not 200<=status<300 or not isinstance(body,dict) or not TASK.fullmatch(str(body.get("id",""))):
                # Even a transport-level 429/503 may arrive after acceptance.
                # The existing runner owns provider cooldown and retry policy.
                # Without an independently proven terminal rejection, no POST.
                evidence={}
                if type(status) is int and 100<=status<=599:
                    evidence["http_status"]=status
                try:
                    delay=int(headers.get("Retry-After",""))
                    if delay>=0: evidence["retry_after_seconds"]=delay
                except (TypeError,ValueError,AttributeError):
                    pass
                return self.journal.transition(key,{"submitting"},"unknown",
                                               failure_code="SUBMIT_REQUIRES_RECONCILE",**evidence)
            return self.journal.transition(key,{"submitting"},"submitted",task_id=body["id"])

    def poll(self,key):
        with self.lock:
            r=self.journal.records[key]
            if r["state"] in FINAL or r["state"]=="awaiting_receipt":
                return deepcopy(r)
            task=r.get("task_id")
            if not task:
                raise HybridBlocked("TASK_ID_UNKNOWN_NO_RESUBMIT")
            try:
                http,body,_=self.gateway.status(task)
            except Exception:
                return self.journal.transition(key,{r["state"]},"unknown",failure_code="STATUS_UNKNOWN")
            if http!=200 or not isinstance(body,dict) or body.get("id")!=task:
                return self.journal.transition(key,{r["state"]},"unknown",failure_code="STATUS_UNVERIFIED")
            state=body.get("status")
            media=body.get("media")
            if state in ("queued","running"):
                target="submitted" if state=="queued" else "rendering"
            elif state=="succeeded" and body.get("partial") is True:
                target="partial"
            elif state=="succeeded" and body.get("partial") is False and isinstance(media,list) and any(
                    isinstance(m,dict) and m.get("kind")=="video" and isinstance(m.get("url"),str)
                    for m in media):
                target="result_ready"
            else:
                # LynByte marks unfinished work failed on restart and DELETE
                # only stops local collection. Neither proves upstream stopped.
                target="unknown"
            return self.journal.transition(key,{r["state"]},target)

    def bind_reconciled_task(self,key,task_id,verify_exact_mapping):
        """Only a trusted backend lookup may recover a lost task ID.

        No prompt substring matching, new-chat guessing or blind resend.
        The lookup must prove owner, exact request key/hash and task ID.
        The pinned LynByte backend needs this lookup hardened before live use.
        """
        with self.lock:
            r=self.journal.records[key]
            if r["state"] not in ("submitting","unknown") or r.get("task_id"):
                raise HybridBlocked("TASK_RECONCILE_NOT_REQUIRED")
            if not TASK.fullmatch(task_id) or verify_exact_mapping(deepcopy(r),task_id) is not True:
                raise HybridBlocked("TASK_MAPPING_UNVERIFIED")
            return self.journal.transition(key,{r["state"]},"submitted",task_id=task_id)

    def close_confirmed_terminal(self,key,reason,verify_terminal):
        """Release the local technical lane only on independent proof that
        no upstream generation remains. Never releases CAM leases or resets
        the key/attempts. A provider refusal stays terminal.
        """
        with self.lock:
            r=self.journal.records[key]
            if r["state"] not in ACTIVE or verify_terminal(deepcopy(r),reason) is not True:
                raise HybridBlocked("TERMINAL_STATE_UNVERIFIED")
            return self.journal.transition(key,{r["state"]},"terminal_rejected",failure_code=reason)

    def bind_validated_media(self,key,media_sha256,verify_media):
        """Verifier is the existing media downloader/ffprobe/QA path.

        It must independently check task identity, local file SHA, aspect,
        duration, print fidelity, garment, motion and exact CAM contract.
        A succeeded flag or text from the renderer is insufficient.
        """
        with self.lock:
            r=self.journal.records[key]
            if r["state"]!="result_ready" or verify_media(deepcopy(r),media_sha256) is not True:
                raise HybridBlocked("MEDIA_QA_NOT_VERIFIED")
            return self.journal.transition(key,{"result_ready"},"awaiting_receipt",
                                           media_sha256=media_sha256)

    def confirm_receipt(self,key,receipt_id,verify_cam_receipt):
        """Use an authoritative CAM readback, not a gateway/webhook success."""
        with self.lock:
            r=self.journal.records[key]
            if r["state"]=="verified":
                if r.get("receipt_id")!=receipt_id:
                    raise HybridBlocked("RECEIPT_ID_CHANGED")
                return deepcopy(r)
            if r["state"]!="awaiting_receipt" or verify_cam_receipt(deepcopy(r),receipt_id) is not True:
                raise HybridBlocked("CAM_RECEIPT_NOT_VERIFIED")
            return self.journal.transition(key,{"awaiting_receipt"},"verified",receipt_id=receipt_id)


@dataclass(frozen=True)
class DispatchResult:
    state: str
    delay_seconds: float


class ContinuousDispatcher:
    """Reuse CAM discovery and the authorized existing execution pipeline.

    In shadow mode only a caller-provided read-only discovery transport runs.
    Live mode is deliberately not exposed as a CLI: ownership cutover,
    renderer compatibility, both leases, genuine telemetry, cooldown state,
    and existing QA/receipt helpers must be integrated in the owning runner.
    """
    def __init__(self,journal,discover,execute,*,shadow=True,reconcile=None,
                 authorized_exclusive_runner=False,renderer_verified=False):
        if not shadow and not (authorized_exclusive_runner and renderer_verified and callable(reconcile)):
            raise HybridBlocked("LIVE_CUTOVER_NOT_VERIFIED")
        self.journal=journal
        self.discover=discover
        self.execute=execute
        self.reconcile=reconcile
        self.shadow=shadow

    def step(self):
        # Never dispatch another CAM job while a prior render is uncertain.
        active=self.journal.active()
        if active:
            if self.shadow:
                return DispatchResult("RECONCILE_EXISTING_RENDER",20)
            key,record=active
            result=self.reconcile(key,record)
            if not isinstance(result,DispatchResult) or result.delay_seconds<2:
                raise HybridBlocked("INVALID_RECONCILE_RESULT")
            if result.state=="CAM_ACK_CONFIRMED" and self.journal.records[key]["state"]!="verified":
                raise HybridBlocked("CAM_RECEIPT_NOT_VERIFIED")
            return result
        try:
            status,body,headers=self.discover(DISCOVERY_PATH)
        except Exception:
            return DispatchResult("DISCOVERY_UNAVAILABLE",30)
        if status==429:
            try: delay=max(30,int(headers.get("Retry-After",120)))
            except (ValueError,TypeError,AttributeError): delay=120
            return DispatchResult("COOLDOWN",delay)
        if status!=200 or not isinstance(body,dict):
            return DispatchResult("DISCOVERY_UNAVAILABLE",30)
        delay=body.get("pollAfterSeconds",120)
        if type(delay) not in (int,float) or not 2<=delay<=86400: delay=120
        if body.get("noClaim") is True or body.get("claimAllowed") is False:
            return DispatchResult("CAM_NOT_READY",delay)
        circuit=body.get("rendererCircuit")
        video_deferred=(body.get("cooldownActive") is True or body.get("videoClaimAllowed") is False
                        or (isinstance(circuit,dict) and circuit.get("state") in ("open","half_open")))
        items=body.get("items")
        if not isinstance(items,list):
            return DispatchResult("INVALID_DISCOVERY",30)
        for item in items:
            if not isinstance(item,dict):
                continue
            contract=item.get("executionContract")
            if not isinstance(contract,dict):
                continue
            action=contract.get("nextAction")
            if action not in ("review_artwork","create_video"):
                continue
            if action=="create_video" and video_deferred:
                continue
            if action=="review_artwork" and body.get("reviewClaimAllowed") is False:
                continue
            if contract.get("authority")!="CAM" or item.get("phase")!=action:
                continue
            if self.shadow:
                return DispatchResult("SHADOW_"+action.upper(),20)
            # Existing execution callback must implement actual CAM claim,
            # provider readiness, exact body/headers, outbox and receipt.
            # Its return controls retry; unknown must not become success.
            result=self.execute(item)
            if not isinstance(result,DispatchResult) or result.delay_seconds<2:
                raise HybridBlocked("INVALID_EXECUTOR_RESULT")
            if result.state not in ("CAM_ACK_CONFIRMED","REVIEW_ACK_CONFIRMED",
                                     "CAM_REJECTED_BEFORE_RENDER","COOLDOWN"):
                # A live executor must not move on after an ambiguous outcome.
                if not self.journal.active():
                    raise HybridBlocked("EXECUTOR_OUTCOME_REQUIRES_RECONCILIATION")
            return result
        return DispatchResult("RENDERER_PROBE_OR_COOLDOWN",delay) if video_deferred else DispatchResult("QUEUE_EMPTY",2)

    def run(self,stop_event):
        while not stop_event.is_set():
            result=self.step()
            stop_event.wait(result.delay_seconds)