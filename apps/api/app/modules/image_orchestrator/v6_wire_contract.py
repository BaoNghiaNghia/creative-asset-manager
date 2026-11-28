"""V6 wire-contract admission helpers, offline candidate only.

Verified against the delivered Muse V6 ZIP. These helpers do not mint leases,
accept QA/storage, authorize a renderer or register a live route. The service
must run these under the existing tenant/resource/pack locks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

from . import policy as p

PROTOCOL = "CAM_MUSE_IMAGE_ADAPTIVE_BATCH_V6"
BUNDLE_SHA256 = "60670dc5a974f60c96ed5dc37bad3db815756f68d194337f5c3973d37e14b3ab"
PINS = {
    "adaptiveBatchPolicy": PROTOCOL,
    "batchPlannerVersion": PROTOCOL,
    "batchPlannerSha256": "936ebc098809f70b3bc77ea3d50b2ff9391cc464b5b2b327fca66b2bcea476ac",
    "packRunnerSha256": "f21295dd35438aff1610fee59b177abda76930e867921f9a8ca217ba534a90c9",
    "nativeRunnerVersion": "CAM_MUSE_IMAGE_NATIVE_V1",
    "nativeRunnerSha256": "1717ae5b26df1ec634115253e3e1a2c49f6dd634328e9194e07be932863b5383",
    "nativePromptTemplateSha256": "455ee7cdc86e6bcbe62e4a68baad566908b98082bfce486f178c1303269db8df",
    "maxBatchRoles": 13,
    "multiOutputSupported": False,
    "multiOutputEvidenceSha256": None,
    "nativeMinimumPx": 1600,
    "publicationMinimumPx": 2000,
}
_ID = re.compile(r"[a-f0-9]{32}\Z")

def _id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None

def manifest_sha256(manifest):
    # Muse uses json.dumps(sort_keys=True) with default separators; this
    # intentionally differs from CAM's compact internal canonical_sha.
    return hashlib.sha256(json.dumps(manifest, sort_keys=True,
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()

def validate_manifest(manifest, sha, *, listing_id, wave, hybrid_role1_authorized=False,
                      expected_protocol=PROTOCOL):
    """Return ordered (call_id, role, attempt_id, method) mappings.

    Single-output only until a separate reviewed provider attestation.
    hybrid_role1_authorized must come from trusted persisted attempt history,
    not a body field. Role 13 dependency readiness is a start-time check.
    """
    p.require(type(wave) is int and wave in p.WAVES, "v6_invalid_wave")
    p.require(isinstance(manifest, dict) and set(manifest) ==
        {"protocol","batchId","listingId","wave","calls"}, "v6_manifest_invalid")
    p.require(manifest["protocol"] == expected_protocol and _id(manifest["batchId"])
        and manifest["listingId"] == listing_id and type(manifest["wave"]) is int
        and manifest["wave"] == wave, "v6_manifest_scope_mismatch")
    calls = manifest["calls"]
    p.require(isinstance(calls, list) and 1 <= len(calls) <= len(p.WAVES[wave]),
        "v6_calls_invalid")
    seen_calls, seen_roles, seen_attempts, rows = set(), set(), set(), []
    for call in calls:
        p.require(isinstance(call, dict) and set(call) ==
            {"callId","roles","outputMapping","methods","attemptIds"}, "v6_call_invalid")
        cid, roles = call["callId"], call["roles"]
        p.require(_id(cid) and cid not in seen_calls, "v6_call_duplicate_or_invalid")
        p.require(isinstance(roles, list) and len(roles) == 1, "v6_multi_output_unattested")
        role = roles[0]
        p.require(type(role) is int and role in p.WAVES[wave] and role not in seen_roles,
            "v6_role_duplicate_or_wrong_wave")
        key = str(role)
        mapping, methods, attempts = call["outputMapping"], call["methods"], call["attemptIds"]
        p.require(isinstance(mapping, dict) and set(mapping) == {"0"}
            and type(mapping["0"]) is int and mapping["0"] == role, "v6_output_mapping_invalid")
        p.require(isinstance(methods, dict) and set(methods) == {key}
            and isinstance(attempts, dict) and set(attempts) == {key}, "v6_role_binding_invalid")
        method, attempt = methods[key], attempts[key]
        p.require(method == "native_imagegen" or (method == "hybrid_compositor"
            and role == 1 and hybrid_role1_authorized is True), "v6_method_not_authorized")
        p.require(_id(attempt) and attempt not in seen_attempts, "v6_attempt_duplicate_or_invalid")
        seen_calls.add(cid); seen_roles.add(role); seen_attempts.add(attempt)
        rows.append((cid, role, attempt, method))
    p.require(p.valid_sha(sha) and hmac.compare_digest(manifest_sha256(manifest), sha),
        "v6_manifest_sha_mismatch")
    return tuple(rows)

def require_infographic_dependencies(role, verified_roles):
    p.require(role != 13 or set(p.INFOGRAPHIC_DEPENDENCIES) <= set(verified_roles),
        "v6_infographic_dependencies_not_verified")

def candidate_registry():
    # Candidate data only: never advertise this as an active capability.
    return {**PINS, "claimEnabled": False, "rendererAllowed": False,
        "autoWaveBarrier": False, "status": "PREPARED_DISABLED",
        "registryStatus": "wire_contract_candidate"}
