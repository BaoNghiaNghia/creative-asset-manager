"""CAM video-only shared creative policy. Worker proposals are not authority.

One private JSON registry, existing worker read/submit and operator approve.
No executable content, credential, renderer call, lease or quota mutation.
"""
from __future__ import annotations

import fcntl
import hashlib
import itertools
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .worker_accounts import registration_file

VERSION = "CAM_MUSE_VIDEO_SHARED_POLICY_V1"
BASE = "/api/v1/muse-bridge/video-policy"
MAX_BYTES = 65536
MAX_HISTORY = 12
MAX_PROPOSALS = 4

class CreativeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    castingVariantCount: int = Field(default=480, ge=100, le=750)
    facePresentation: Literal["natural_visible", "face_and_print_visible"] = "natural_visible"
    expressionStyle: Literal["natural", "warm", "calm"] = "natural"
    motionStyle: Literal["natural_everyday", "subtle_posture", "small_step"] = "natural_everyday"
    lightingStyle: Literal["natural_soft", "existing_reference"] = "natural_soft"

class CreativePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    castingVariantCount: int | None = Field(default=None, ge=100, le=750)
    facePresentation: Literal["natural_visible", "face_and_print_visible"] | None = None
    expressionStyle: Literal["natural", "warm", "calm"] | None = None
    motionStyle: Literal["natural_everyday", "subtle_posture", "small_step"] | None = None
    lightingStyle: Literal["natural_soft", "existing_reference"] | None = None

class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expectedRevision: int = Field(ge=1)
    changes: CreativePatch

class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expectedRevision: int = Field(ge=1)
    action: Literal["approve", "reject", "rollback"]
    proposalId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    rollbackRevision: int | None = Field(default=None, ge=1)

def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()

def _now():
    return datetime.now(timezone.utc).isoformat()

def _snapshot(revision, settings, actor, time=None):
    values = CreativeSettings.model_validate(settings).model_dump()
    core = {"policyVersion": VERSION, "revision": revision, "settings": values}
    return {**core, "sha256": _digest(core), "updatedAtUtc": time, "updatedBy": actor}

def _default():
    return {"active": _snapshot(1, CreativeSettings().model_dump(), "baseline"),
            "history": [], "proposals": []}

def _validate_snapshot(value):
    if not isinstance(value, dict) or value.get("policyVersion") != VERSION:
        raise ValueError("Invalid shared video policy")
    revision = value.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("Invalid shared video policy revision")
    expected = _snapshot(revision, value["settings"], value.get("updatedBy"), value.get("updatedAtUtc"))
    if expected != value:
        raise ValueError("Shared video policy digest or schema differs")
    return expected

def _directory():
    # Use the writable persistent systemd StateDirectory, as cooldown does.
    # The protected installation/registration tree stays read-only.
    root = os.environ.get("STATE_DIRECTORY")
    if not root:
        return registration_file(os.environ).parent / "muse-video-policy"
    parent = Path(root)
    if not parent.is_absolute() or ":" in root or ".." in parent.parts:
        raise ValueError("Invalid shared policy state root")
    meta = parent.lstat()
    if not stat.S_ISDIR(meta.st_mode) or (meta.st_uid != os.geteuid() and os.geteuid() != 0):
        raise ValueError("Shared policy state root owner invalid")
    directory = parent / "muse-video-policy"
    directory.mkdir(mode=0o700, exist_ok=True)
    _check_dir(directory)
    return directory

def _check_dir(path):
    meta = path.lstat()
    if (not stat.S_ISDIR(meta.st_mode) or stat.S_IMODE(meta.st_mode) != 0o700
            or (meta.st_uid != os.geteuid() and os.geteuid() != 0)):
        raise ValueError("Shared policy directory is not private")

def _read(path):
    if not path.exists() and not path.is_symlink():
        return _default()
    _check_dir(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_BYTES:
            raise ValueError("Shared policy file permissions or size invalid")
        state = json.loads(stream.read(MAX_BYTES + 1))
    if not isinstance(state, dict) or set(state) != {"active", "history", "proposals"}:
        raise ValueError("Shared policy registry schema differs")
    _validate_snapshot(state["active"])
    if not isinstance(state["history"], list) or len(state["history"]) > MAX_HISTORY:
        raise ValueError("Shared policy history invalid")
    for previous in state["history"]:
        _validate_snapshot(previous)
    if not isinstance(state["proposals"], list) or len(state["proposals"]) > MAX_PROPOSALS:
        raise ValueError("Shared policy proposals invalid")
    for p in state["proposals"]:
        if (not isinstance(p, dict) or p.get("workerId") not in ("muse-01", "muse-02", "legacy")
                or p.get("expectedRevision") != state["active"]["revision"]):
            raise ValueError("Shared policy proposal invalid")
        change = ChangeRequest.model_validate({"expectedRevision": p["expectedRevision"], "changes": p["changes"]})
        if p.get("proposalId") != _proposal_id(p["workerId"], change):
            raise ValueError("Shared policy proposal identity invalid")
    return state

def registry():
    try:
        return _read(_directory() / "registry.json")
    except (OSError, ValueError, TypeError, KeyError, ValidationError) as exc:
        raise HTTPException(503, "CAM shared video policy registry unavailable") from exc

def active():
    return registry()["active"]

def _write(path, state):
    encoded = _canonical(state) + b"\n"
    if len(encoded) > MAX_BYTES:
        raise ValueError("Shared policy registry exceeds size bound")
    fd, tmp = tempfile.mkstemp(prefix=".registry-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        sync = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(sync)
        finally:
            os.close(sync)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def _mutate(expected, action):
    try:
        directory = _directory()
        _check_dir(directory)
        fd = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("Shared policy lock invalid")
            fcntl.flock(fd, fcntl.LOCK_EX)
            path = directory / "registry.json"
            state = _read(path)
            if state["active"]["revision"] != expected:
                raise HTTPException(409, "Shared video policy revision changed; refresh config")
            result = action(state)
            _write(path, state)
            return result
        finally:
            os.close(fd)
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, KeyError, ValidationError) as exc:
        raise HTTPException(503, "CAM shared video policy write unavailable") from exc

def _changes(request):
    values = request.changes.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "At least one supported creative setting is required")
    return values

def _proposal_id(worker, request):
    return _digest({"workerId": worker, "expectedRevision": request.expectedRevision,
                    "changes": _changes(request)})[:32]

def propose(worker, request):
    if worker not in ("muse-01", "muse-02", "legacy"):
        raise HTTPException(403, "Video worker identity required")
    changes = _changes(request)
    pid = _proposal_id(worker, request)
    def submit(state):
        for p in state["proposals"]:
            if p["proposalId"] == pid:
                return {"ok": True, "status": "PENDING_OPERATOR_APPROVAL", **p}
        if all(state["active"]["settings"].get(k) == v for k,v in changes.items()):
            return {"ok": True, "status": "ALREADY_ACTIVE", "revision": request.expectedRevision}
        # One pending proposal per worker: bounded storage, not a publish grant.
        state["proposals"] = [p for p in state["proposals"] if p["workerId"] != worker]
        if len(state["proposals"]) >= MAX_PROPOSALS:
            raise HTTPException(409, "Shared video policy proposal queue full")
        p = {"proposalId": pid, "workerId": worker, "expectedRevision": request.expectedRevision,
             "changes": changes, "submittedAtUtc": _now()}
        state["proposals"].append(p)
        return {"ok": True, "status": "PENDING_OPERATOR_APPROVAL", **p}
    return _mutate(request.expectedRevision, submit)

def _publish(state, settings, actor):
    old = state["active"]
    if old["settings"] == settings:
        return {"ok": True, "status": "ALREADY_ACTIVE", "active": old}
    state["history"] = ([old] + state["history"])[:MAX_HISTORY]
    state["active"] = _snapshot(old["revision"] + 1, settings, actor, _now())
    state["proposals"] = []  # All proposals are tied to the previous revision.
    return {"ok": True, "status": "PUBLISHED", "active": state["active"]}

def publish(request):
    changes = _changes(request)
    return _mutate(request.expectedRevision,
                   lambda s: _publish(s, {**s["active"]["settings"], **changes}, "operator"))

def decide(request):
    def apply(state):
        if request.action == "rollback":
            if request.proposalId is not None or request.rollbackRevision is None:
                raise HTTPException(422, "Rollback revision required")
            previous = next((p for p in state["history"] if p["revision"] == request.rollbackRevision), None)
            if not previous:
                raise HTTPException(404, "Shared video policy revision not retained")
            return _publish(state, previous["settings"], "operator_rollback")
        if request.rollbackRevision is not None or not request.proposalId:
            raise HTTPException(422, "Proposal ID required")
        p = next((p for p in state["proposals"] if p["proposalId"] == request.proposalId), None)
        if not p:
            raise HTTPException(404, "Shared video policy proposal not found")
        if request.action == "reject":
            state["proposals"].remove(p)
            return {"ok": True, "status": "REJECTED", "proposalId": p["proposalId"]}
        return _publish(state, {**state["active"]["settings"], **p["changes"]},
                        "operator_approved_" + p["workerId"])
    return _mutate(request.expectedRevision, apply)

def config():
    return {**active(), "scope": "video_only_new_story_lite_jobs",
            "proposalPath": BASE + "/proposals",
            "operatorPublishPath": BASE + "/operator",
            "operatorDecisionPath": BASE + "/operator/decision",
            "operatorApprovalRequired": True,
            "policyPinnedAt": "video_job_claim",
            "castingMode": "deterministic_synthetic_prompt_profiles_not_face_assets",
            "verifiedReferenceFacePool": False,
            "instructions": (
                "Use live per-job storySpecV33 and its pinned sharedVideoPolicy. "
                "Only new reference preparation uses a casting profile; never replace a valid approved hero solely to match it. "
                "If the user requests a global creative change, submit a structured proposal with expectedRevision; "
                "do not modify an individual runner instead or report it active before operator approval. "
                "Do not copy credentials, cooldowns or leases. Existing pinned/legacy jobs keep their contracts."
            )}

# Profiles are synthetic casting descriptions, not real identity embeddings.
# Deterministic shuffled order avoids a biased prefix when pool size changes.
_AGES = ("adult in their 20s", "adult in their 30s", "adult in their 40s",
         "adult in their 50s", "adult in their 60s")
_SHAPES = ("oval", "round", "angular", "heart-shaped", "oblong")
_HAIR = ("short textured", "neat cropped", "medium straight", "medium wavy",
         "long natural", "short curly")
_COLORS = ("black", "brown", "dark blonde", "auburn", "salt-and-pepper")
_PROFILES = sorted(itertools.product(_AGES, _SHAPES, _HAIR, _COLORS),
                   key=lambda p: _digest({"castingProfile": p, "version": VERSION}))

def casting_profile(snapshot, listing_key, artwork_sha, ratio, hero_request_id):
    count = snapshot["settings"]["castingVariantCount"]
    index = int(_digest([listing_key, artwork_sha, ratio, hero_request_id]), 16) % count
    age, shape, hair, color = _PROFILES[index]
    return {"profileId": f"casting-{index+1:04d}", "description":
            f"{age}, {shape} face shape, {hair} {color} hair, realistic natural features"}

def apply_story(story, snapshot, listing_key, artwork_sha):
    snapshot = _validate_snapshot(snapshot)
    settings = snapshot["settings"]
    expressions = {"natural": "Natural expressions, blinking and gaze are welcome.",
                   "warm": "Use a relaxed warm expression or gentle natural smile.",
                   "calm": "Use a calm relaxed expression, with natural blinking and gaze."}
    motions = {"natural_everyday": "Use one coherent natural everyday action.",
               "subtle_posture": "Prefer a gentle natural weight or posture shift.",
               "small_step": "If suitable for the existing scene, take one small natural step; otherwise settle naturally."}
    framing = {"natural_visible": "Keep the face naturally visible; no timed face quota.",
               "face_and_print_visible": "Keep the face and full print visible together where naturally feasible; no timed face quota."}
    light = {"natural_soft": "Prefer soft natural light for a new reference.",
             "existing_reference": "Preserve lighting appropriate to the existing reference scene."}
    story["sharedVideoPolicy"] = snapshot
    for ratio, plan in story["aspectPlans"].items():
        if "referenceImagePrompt" not in plan:
            # Pre-upgrade non-reference-first prompts are not altered.
            continue
        profile = casting_profile(snapshot, listing_key, artwork_sha, ratio, plan["heroRequestId"])
        plan["castingProfile"] = profile
        plan["referenceImagePrompt"] += (
            " For a newly needed reference only, use this advisory synthetic casting variant: "
            + profile["description"] + ". Fit the design's intended audience; preserve any approved suitable reference. "
            + light[settings["lightingStyle"]] + " " + framing[settings["facePresentation"]])
        plan["prompt"] += (" Keep the exact hero background, objects, lighting and locked camera unchanged. "
                           "Animate the person only; preserve face, identity, clothing, print and trousers. "
                           + expressions[settings["expressionStyle"]] + " "
                           + motions[settings["motionStyle"]] + " "
                           + framing[settings["facePresentation"]])
    story["narrativePrompt"] = story["aspectPlans"]["9:16"]["prompt"]
    return story
