#!/usr/bin/env python3
"""Read-only, secret-free P2 CDN readiness check.

Run this BEFORE changing CAM_ETSY_IMAGE_CDN_ENABLED. Never uploads images,
changes production config, prints credentials or exposes tenant-specific URLs.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from production_env import parse_environment_file, SafeConfigurationError  # noqa: E402


def check(values: dict[str, str], *, probe: bool = False) -> dict[str, object]:
    base = values.get("CAM_ETSY_IMAGE_CDN_WORKER_URL", "").rstrip("/")
    secret = values.get("CAM_ETSY_IMAGE_CDN_HMAC_HEX", "")
    canaries = [x.strip() for x in
                values.get("CAM_ETSY_IMAGE_CDN_CANARY_TENANTS", "").split(",")
                if x.strip()]
    missing = [
        k for k in ("CAM_ETSY_IMAGE_CDN_WORKER_URL",
                    "CAM_ETSY_IMAGE_CDN_HMAC_HEX",
                    "CAM_ETSY_IMAGE_CDN_CANARY_TENANTS")
        if not values.get(k, "").strip()
    ]
    parsed = urlsplit(base)
    valid_origin = bool(
        parsed.scheme == "https" and parsed.hostname and
        not (parsed.username or parsed.password or parsed.port or
             parsed.path or parsed.query or parsed.fragment))
    valid_key = bool(re.fullmatch(r"[0-9a-fA-F]{64,128}", secret))
    enabled = values.get("CAM_ETSY_IMAGE_CDN_ENABLED", "").lower() in ("1", "true")
    report: dict[str, object] = {
        "enabled": enabled,
        "configured": bool(not missing and valid_origin and valid_key),
        "canary_count": len(canaries),
        "missing_keys": missing,
        "worker_origin_valid": valid_origin,
        "signing_key_valid": valid_key,
        "csp_allows_worker": None,
        "worker_signature_probe": "not_run",
        "ready_to_enable": False,
    }
    if not probe or not report["configured"]:
        return report

    origin = f"{parsed.scheme}://{parsed.hostname}"
    app = values.get("PUBLIC_APP_URL", "").rstrip("/")
    try:
        with urlopen(Request(app + "/etsy-spy", method="HEAD"), timeout=5) as resp:
            csp = resp.headers.get("Content-Security-Policy", "")
        directives = {part.split(None, 1)[0]: part.split()[1:]
                      for part in csp.split(";") if part.split()}
        # Wildcards are not allowed: authorize exactly this Worker origin.
        report["csp_allows_worker"] = origin in directives.get("img-src", [])
    except (HTTPError, URLError, OSError, ValueError):
        report["csp_allows_worker"] = False

    # Synthetic object cannot exist; a HEAD 404 with a valid signature means
    # the private Worker and the deployed HMAC key agree. 403/503 fail closed.
    tenant = next((x for x in canaries if x != "*"), "")
    if not tenant:
        report["worker_signature_probe"] = "no_specific_canary_tenant"
        return report
    key_bytes = bytes.fromhex(secret)
    tenant_hash = hmac.new(key_bytes, tenant.encode(), hashlib.sha256).hexdigest()[:24]
    object_key = ("v1/" + tenant_hash + "/sp-" + "0" * 40 +
                  "/01/thumbnail/" + "0" * 64 + ".png")
    path = "/v1/media/" + object_key
    expiry = int(time.time()) + 30
    message = f"HEAD\n{path}\n{expiry}"
    signature = hmac.new(key_bytes, message.encode(), hashlib.sha256).hexdigest()
    signed = f"{base}{path}?exp={expiry}&sig={signature}"
    try:
        with urlopen(Request(signed, method="HEAD"), timeout=5) as resp:
            status = resp.status
    except HTTPError as exc:
        status = exc.code
    except (URLError, OSError, ValueError):
        status = 0
    report["worker_signature_probe"] = {
        404: "valid_signature_private_object_absent",
        403: "signature_mismatch_or_expired",
        503: "worker_or_r2_not_configured",
        0: "network_unavailable",
    }.get(status, "unexpected_worker_response")
    report["ready_to_enable"] = (
        report["csp_allows_worker"] is True and status == 404)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/creative-asset-manager/production.env")
    parser.add_argument("--probe", action="store_true",
                        help="Read-only CSP and signed synthetic Worker HEAD probes")
    args = parser.parse_args()
    try:
        values = parse_environment_file(Path(args.env_file))
    except SafeConfigurationError:
        print(json.dumps({"ready_to_enable": False, "error": "config_unavailable"}))
        return 2
    report = check(values, probe=args.probe)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ready_to_enable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
