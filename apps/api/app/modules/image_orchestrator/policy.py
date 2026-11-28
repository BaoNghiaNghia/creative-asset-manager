"""CAM Image Orchestrator V1.2: deterministic, renderer-neutral P0 policy.

All timestamps are UTC epoch seconds supplied by CAM. Workers never supply
authoritative time, storage verification, lease ownership or QA acceptance.
This module has no I/O. The future repository must lock pack/batch rows and
commit every transition atomically, before acknowledging it to a worker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field

PROTOCOL = "CAM_IMAGE_ORCHESTRATOR_V1_2"
SKILL_VERSION = "2026-11-25.skill16-tagfix"
SKILL_SHA256 = "cbb70b5a1fd5f3d83a453f4f47a34bd6a379299ba00953fbbb8b7984e97a856f"
SUPERSEDED_SKILL_SHA256 = "8826d1c19223b3d64f7fe229ebb53767faff6f23acf5999d8b42882c345e26b6"
GARMENTS = frozenset({"C1717", "G500", "G180", "G185"})
ROLE_CONTRACT = "CAM_ROLE_01_WORN_04_FOLDED_PROPS_V1"
PLACEMENT_CONTRACT = "CAM_PRINT_TOP_150MM_V3"
QA_POLICY = "CAM_IMAGE_QA_V1"
WAVES = {1: tuple(range(1, 5)), 2: tuple(range(5, 14))}
INFOGRAPHIC_DEPENDENCIES = (1, 5, 6, 7)
LEASE_SECONDS = 3600
HEARTBEAT_SECONDS = 60
BARRIER_SECONDS = 86400
ACK_REPLAY_SECONDS = 86400
MAX_ROLE_ATTEMPTS = 3  # one first attempt + two verified-failure retries
MAX_WAVE_CLAIMS = 5
PILOT_MIN_NATIVE = 1600
PUBLICATION_MIN_NATIVE = 2000
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_LISTING = re.compile(r"[0-9]{5,20}\Z")


class Conflict(ValueError):
    """Stable bounded error code; never include secret or provider payload."""


def require(condition: bool, code: str):
    if not condition:
        raise Conflict(code)


def canonical_sha(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_sha(value: str) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


@dataclass(frozen=True)
class PackIdentity:
    tenant: str
    listing: str
    artwork_sha: str
    garment: str
    revision: int
    placement_sha: str
    source_sha: str
    skill_sha: str = SKILL_SHA256

    def __post_init__(self):
        require(isinstance(self.tenant, str) and 0 < len(self.tenant) <= 255,
                "invalid_tenant")
        require(isinstance(self.listing, str) and _LISTING.fullmatch(self.listing) is not None,
                "invalid_listing")
        require(self.garment in GARMENTS, "invalid_garment")
        require(type(self.revision) is int and 1 <= self.revision <= 10000, "invalid_revision")
        require(all(valid_sha(x) for x in (self.artwork_sha, self.placement_sha,
                                           self.source_sha, self.skill_sha)), "invalid_sha")
        require(self.skill_sha == SKILL_SHA256, "skill_not_pinned_for_new_packs")

    @property
    def pack_id(self) -> str:
        # Existing ListingImage.pack_id is VARCHAR(64). Hash the entire
        # tenant-scoped tuple; never key by only the first 12 chars of artwork.
        return canonical_sha([PROTOCOL, self.tenant, self.listing,
                              self.artwork_sha, self.garment, self.revision])

    def role_key(self, role: int) -> str:
        require(type(role) is int and 1 <= role <= 13, "invalid_role")
        return canonical_sha([self.pack_id, self.placement_sha, self.skill_sha, role])


@dataclass(frozen=True)
class StoredReceipt:
    """Construct only from server-side verified Drive result + accepted QA.

    A runner's storageVerified boolean is never a substitute for this adapter.
    No raw lease, bearer, URL or source PNG is persisted in a receipt.
    """
    image_sha: str
    width: int
    height: int
    drive_file_id: str
    qa_image_sha: str
    qa_status: str
    storage_verified: bool
    placement_sha: str
    publication_minimum_px: int = PUBLICATION_MIN_NATIVE
    generation_method: str | None = None
    qa_record_sha: str | None = None
    operator_policy_receipt_sha: str | None = None

    @property
    def publication_scope(self):
        return (self.publication_minimum_px,self.generation_method,
                self.qa_record_sha,self.operator_policy_receipt_sha)

    @property
    def publication_ready(self) -> bool:
        legacy=self.publication_scope==(PUBLICATION_MIN_NATIVE,None,None,None)
        native=(self.generation_method=="NATIVE_IMAGEGEN"
                and type(self.publication_minimum_px) is int
                and self.publication_minimum_px in (1600,PUBLICATION_MIN_NATIVE)
                and valid_sha(self.qa_record_sha)
                and valid_sha(self.operator_policy_receipt_sha))
        return bool((legacy or native) and self.storage_verified is True and self.qa_status=="pass"
                    and self.width==self.height and self.width>=self.publication_minimum_px)


@dataclass
class Role:
    status: str = "pending"
    attempts: int = 0
    attempt_id: str | None = None
    receipt: StoredReceipt | None = None
    failure_code: str | None = None


@dataclass
class Lease:
    owner: str
    wave: int
    epoch: int
    expires_at: int
    command_hash: str = field(repr=False)
    image_hash: str = field(repr=False)
    # Historical leases lack a baseline and cannot be refunded.
    verified_at_claim: int | None = None
    # V6.1: only bounded manifest metadata, never raw lease credentials.
    adaptive_batches: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Grant:
    epoch: int
    expires_at: int
    command_token: str = field(repr=False)
    image_token: str = field(repr=False)


@dataclass
class Pack:
    identity: PackIdentity
    owner: str | None = None
    epoch: int = 0
    lease: Lease | None = None
    roles: dict[int, Role] = field(default_factory=lambda: {i: Role() for i in range(1, 14)})
    wave_claims: dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0})
    # Bounded durable ACK metadata; never retain raw tokens.
    acks: dict[str, dict] = field(default_factory=dict, repr=False)
    used_attempt_ids: set[str] = field(default_factory=set, repr=False)

    def wave_complete(self, wave: int) -> bool:
        return all(self.roles[i].status == "verified" for i in WAVES[wave])

    @property
    def complete(self) -> bool:
        return self.wave_complete(1) and self.wave_complete(2)

    @property
    def publishable(self) -> bool:
        return self.complete and all(r.receipt and r.receipt.publication_ready
                                     for r in self.roles.values())

    @property
    def uncertain(self) -> bool:
        return any(r.status in {"dispatch_uncertain", "reconcile_required", "upload_pending"}
                   for r in self.roles.values())


@dataclass(frozen=True)
class Batch:
    batch_id: str
    tenant: str
    pack_ids: tuple[str, ...]
    created_at: int

    def __post_init__(self):
        require(isinstance(self.batch_id, str) and _ID.fullmatch(self.batch_id) is not None,
                "invalid_batch")
        require(bool(self.pack_ids) and len(set(self.pack_ids)) == len(self.pack_ids)
                and all(valid_sha(p) for p in self.pack_ids), "invalid_batch_members")


def barrier(batch: Batch, packs: list[Pack], now: int) -> dict:
    by_id = {p.identity.pack_id: p for p in packs}
    require(set(by_id) == set(batch.pack_ids) and len(by_id) == len(packs)
            and all(p.identity.tenant == batch.tenant for p in packs), "batch_scope_mismatch")
    ready = [pid for pid in batch.pack_ids if by_id[pid].wave_complete(1)]
    isolated = []
    for pid in batch.pack_ids:
        p = by_id[pid]
        if pid in ready:
            continue
        terminal_hold = any(p.roles[i].status == "hold" for i in WAVES[1])
        if terminal_hold or now >= batch.created_at + BARRIER_SECONDS:
            isolated.append(pid)
    waiting = [pid for pid in batch.pack_ids if pid not in ready and pid not in isolated]
    # Isolation changes barrier eligibility only, not the in-flight lease or
    # ownership. The repository must retain isolated memberships in recovery.
    return {"open": not waiting, "wave2_eligible": ready if not waiting else [],
            "isolated": isolated, "waiting": waiting}


def claim(pack: Pack, worker: str, wave: int, now: int, *,
          wave2_allowed: bool = False, flow_running: bool = False,
          capability_verified: bool = False, resource_granted: bool = False) -> Grant:
    require(flow_running and capability_verified and resource_granted, "image_claim_disabled")
    require(isinstance(worker, str) and _ID.fullmatch(worker) is not None, "invalid_worker")
    require(type(wave) is int and wave in WAVES, "invalid_wave")
    require(pack.lease is None or pack.lease.expires_at <= now, "already_claimed")
    require(not pack.uncertain, "reconciliation_required")
    require(pack.owner is None or pack.owner == worker, "pack_owner_mismatch")
    require(wave == 1 or (wave2_allowed and pack.wave_complete(1)), "wave_barrier_closed")
    require(not pack.wave_complete(wave), "wave_already_complete")
    require(any(pack.roles[i].status == "pending" for i in WAVES[wave]), "no_claimable_roles")
    require(pack.wave_claims[wave] < MAX_WAVE_CLAIMS, "wave_claim_limit")
    c, i = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    pack.epoch += 1
    pack.owner = worker
    pack.wave_claims[wave] += 1
    pack.lease = Lease(worker, wave, pack.epoch, now + LEASE_SECONDS, digest(c), digest(i),
                       sum(pack.roles[n].status == "verified" for n in WAVES[wave]))
    return Grant(pack.epoch, pack.lease.expires_at, c, i)


def authorized(pack: Pack, worker: str, epoch: int, token: str, now: int, *,
               kind: str = "image") -> Lease:
    lease = pack.lease
    require(lease is not None and pack.owner == worker and lease.owner == worker
            and lease.epoch == epoch and lease.expires_at > now, "lease_invalid")
    require(kind in {"image", "command"} and isinstance(token, str), "lease_invalid")
    target = lease.image_hash if kind == "image" else lease.command_hash
    require(hmac.compare_digest(target, digest(token)), "lease_invalid")
    return lease


def heartbeat(pack: Pack, worker: str, epoch: int, command_token: str, now: int) -> int:
    lease = authorized(pack, worker, epoch, command_token, now, kind="command")
    # One atomic persisted expiration governs both independently authenticated tokens.
    lease.expires_at = max(lease.expires_at, now + LEASE_SECONDS)
    return lease.expires_at


def role_start(pack: Pack, worker: str, epoch: int, image_token: str, now: int,
               role: int, attempt_id: str, *, flow_running: bool = False,
               resource_granted: bool = False) -> bool:
    lease = authorized(pack, worker, epoch, image_token, now)
    require(type(role) is int and role in WAVES[lease.wave], "role_outside_wave")
    require(isinstance(attempt_id, str) and _ID.fullmatch(attempt_id) is not None,
            "invalid_attempt_id")
    r = pack.roles[role]
    # Replaying a lost role-start ACK never grants a second send.
    if r.attempt_id == attempt_id:
        return False
    require(flow_running and resource_granted, "image_start_disabled")
    require(not pack.uncertain, "reconciliation_required")
    require(r.status == "pending" and r.attempts < MAX_ROLE_ATTEMPTS, "role_not_pending")
    require(attempt_id not in pack.used_attempt_ids, "attempt_id_reused")
    if role == 13:
        require(all(pack.roles[i].status == "verified" and pack.roles[i].receipt
                    and pack.roles[i].receipt.qa_status == "pass"
                    for i in INFOGRAPHIC_DEPENDENCIES), "infographic_dependencies_missing")
    r.attempts += 1
    pack.used_attempt_ids.add(attempt_id)
    r.attempt_id = attempt_id
    r.status = "dispatch_uncertain"  # commit BEFORE invoking renderer
    return True


def verify_role(pack: Pack, worker: str, epoch: int, image_token: str, now: int,
                role: int, attempt_id: str, receipt: StoredReceipt) -> str:
    lease = authorized(pack, worker, epoch, image_token, now)
    require(type(role) is int and role in WAVES[lease.wave], "role_outside_wave")
    r = pack.roles[role]
    require(r.attempt_id == attempt_id, "attempt_mismatch")
    require(valid_sha(receipt.image_sha) and receipt.qa_image_sha == receipt.image_sha
            and receipt.qa_status == "pass" and receipt.storage_verified is True
            and receipt.placement_sha == pack.identity.placement_sha
            and isinstance(receipt.drive_file_id, str) and _ID.fullmatch(receipt.drive_file_id)
            is not None, "invalid_verified_receipt")
    require(type(receipt.width) is int and type(receipt.height) is int
            and receipt.width == receipt.height and PILOT_MIN_NATIVE <= receipt.width <= 8192,
            "invalid_native_dimensions")
    if r.status == "verified":
        require(r.receipt == receipt, "immutable_role_receipt")
        return "already_verified"
    require(r.status in {"dispatch_uncertain", "upload_pending"}, "role_not_in_progress")
    if role == 13:
        require(all(pack.roles[i].status == "verified" for i in INFOGRAPHIC_DEPENDENCIES),
                "infographic_dependencies_missing")
    r.receipt = receipt
    r.status = "verified"
    r.failure_code = None
    return "verified"


def confirmed_failure(pack: Pack, role: int, attempt_id: str, failure_code: str, *,
                      terminal: bool, proof_of_no_usable_result: bool = False):
    """Internal reconciliation action only, after authentic renderer evidence.

    Lease expiration or a worker timeout alone is NOT a confirmed failure.
    The caller must verify proof and audit its source; no public bool flag.
    """
    r = pack.roles[role]
    require(proof_of_no_usable_result is True and r.attempt_id == attempt_id
            and r.status in {"dispatch_uncertain", "upload_pending", "reconcile_required"},
            "failure_not_confirmed")
    require(isinstance(failure_code, str) and _ID.fullmatch(failure_code) is not None,
            "invalid_failure_code")
    r.failure_code = failure_code
    r.status = "hold" if terminal or r.attempts >= MAX_ROLE_ATTEMPTS else "pending"


def expire(pack: Pack, now: int) -> bool:
    if pack.lease is None or pack.lease.expires_at > now:
        return False
    pack.lease = None
    for r in pack.roles.values():
        if r.status in {"dispatch_uncertain", "upload_pending"}:
            r.status = "reconcile_required"
    return True


def complete(pack: Pack, worker: str, epoch: int, command_token: str,
             now: int, outcome: str) -> dict:
    require(outcome in {"success", "partial", "failed"}, "invalid_outcome")
    require(isinstance(command_token, str), "lease_invalid")
    key = canonical_sha([worker, epoch, digest(command_token), outcome])
    ack = pack.acks.get(key)
    if ack and now <= ack["expires_at"]:
        return {**ack["result"], "ackReplayed": True}
    lease = authorized(pack, worker, epoch, command_token, now, kind="command")
    roles = WAVES[lease.wave]
    verified = [i for i in roles if pack.roles[i].status == "verified"]
    missing = [i for i in roles if i not in verified]
    require(not pack.uncertain, "reconciliation_required")
    expected = "success" if not missing else "partial" if verified else "failed"
    require(outcome == expected, "outcome_receipt_mismatch")
    # Refund a claim only for new, durable verified roles. A failed or empty
    # slice still consumes the existing five-claim no-progress budget.
    baseline = lease.verified_at_claim
    progress = type(baseline) is int and 0 <= baseline < len(verified)
    if progress:
        require(pack.wave_claims[lease.wave] > 0, "wave_claim_state_invalid")
        pack.wave_claims[lease.wave] -= 1
    result = {"outcome": outcome, "wave": lease.wave, "verifiedRoles": verified,
              "missingRoles": missing, "packComplete": pack.complete,
              "productiveSlice": progress,
              "remainingNoProgressClaims": MAX_WAVE_CLAIMS - pack.wave_claims[lease.wave]}
    pack.acks = {k: v for k, v in pack.acks.items() if v["expires_at"] >= now}
    pack.acks[key] = {"expires_at": now + ACK_REPLAY_SECONDS, "result": result}
    pack.lease = None
    return {**result, "ackReplayed": False}


def release_owner(pack: Pack, *, operator_approved: bool = False):
    """Manual recovery only; never infer a failed render from timeout."""
    require(operator_approved and pack.lease is None and not pack.uncertain,
            "unsafe_owner_release")
    pack.owner = None
