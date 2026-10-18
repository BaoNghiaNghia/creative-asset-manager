from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.modules.branding_dna.signed_job_envelope import sign_preflight_envelope, envelope_signing_bytes


def claim(now: int):
    return {
        "version": 1, "purpose": "preflight_only", "scan_enabled": False,
        "key_id": "test-v1", "tenant_id": str(uuid4()), "shop_id": str(uuid4()),
        "task_id": str(uuid4()), "snapshot_id": str(uuid4()),
        "agent_id": str(uuid4()), "lease_id": str(uuid4()),
        "lease_generation": 1, "platform": "etsy",
        "scope_sha256": "a" * 64, "nonce": str(uuid4()),
        "issued_at": now, "expires_at": now + 90,
        "max_pages": 5, "max_gallery_items": 20,
    }


def test_framing_uses_real_line_feed_bytes():
    packet = claim(int(datetime.now(timezone.utc).timestamp()))
    assert envelope_signing_bytes(packet).startswith(
        b"branding-dna-preflight-v1" + bytes([10])
    )


def test_signs_bounded_preflight_only_packet():
    now = int(datetime.now(timezone.utc).timestamp())
    signed = sign_preflight_envelope(claim(now), Ed25519PrivateKey.generate(), now=now)
    assert set(signed) == set(claim(now)) | {"signature"}
    assert len(signed["signature"]) == 86
    assert signed["scan_enabled"] is False


@pytest.mark.parametrize("changes", [
    {"scan_enabled": True}, {"purpose": "execute"}, {"platform": "shopify"},
    {"max_pages": 6}, {"max_gallery_items": 21},
    {"expires_at": 9999999999}, {"key_id": "test\\nattack"},
    {"lease_generation": True}, {"scope_sha256": "z" * 64},
])
def test_rejects_bypass_or_inconsistent_claim(changes):
    now = int(datetime.now(timezone.utc).timestamp())
    packet = claim(now)
    packet.update(changes)
    with pytest.raises(ValueError):
        sign_preflight_envelope(packet, Ed25519PrivateKey.generate(), now=now)




def test_python_signature_verifies_in_node_worker():
    import json
    from pathlib import Path
    import shutil
    import subprocess

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    if not shutil.which("node"):
        pytest.skip("cross-runtime verification requires Node.js")
    now = int(datetime.now(timezone.utc).timestamp())
    packet = sign_preflight_envelope(claim(now), Ed25519PrivateKey.from_private_bytes(bytes([31] * 32)), now=now)
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([31] * 32))
    public_pem = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    expected_keys = ("tenant_id", "shop_id", "task_id", "snapshot_id",
                     "agent_id", "lease_id", "lease_generation", "platform", "scope_sha256")
    payload = {"packet": packet, "keys": {packet["key_id"]: public_pem},
               "expected": {key: packet[key] for key in expected_keys}, "now": now}
    code = (
        "import {verifyPreflightEnvelope} from './apps/branding-dna-worker/src/signed_job_envelope.mjs';"
        "const input=JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(verifyPreflightEnvelope(input.packet,input)));"
    )
    run = subprocess.run(["node", "--input-type=module", "-e", code, json.dumps(payload)],
                         cwd=Path(__file__).resolve().parents[5], capture_output=True,
                         text=True, timeout=10, check=True)
    assert json.loads(run.stdout) == {k: v for k, v in packet.items() if k != "signature"}

def test_rejects_stale_or_future_issued_claim():
    now = int(datetime.now(timezone.utc).timestamp())
    for times in [(now - 600, now - 500), (now + 100, now + 200), (now, now)]:
        packet = claim(now)
        packet["issued_at"], packet["expires_at"] = times
        with pytest.raises(ValueError):
            sign_preflight_envelope(packet, Ed25519PrivateKey.generate(), now=now)
