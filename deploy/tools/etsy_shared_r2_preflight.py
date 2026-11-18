#!/usr/bin/env python3
"""Read-only preflight for Etsy Spy on the existing CAM video R2/Worker.

Never prints credentials or sends source images. A 404 for a correctly signed,
random-looking synthetic image HEAD proves that the deployed shared Worker
understands the new image path and the existing R2 binding.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import time

if __package__:
    from .production_env import SafeConfigurationError, parse_environment_file
else:  # Direct file execution remains supported for operators.
    from production_env import SafeConfigurationError, parse_environment_file


def _valid_https_origin(value: str) -> str | None:
    parsed = urlsplit(value.strip().rstrip("/"))
    if (parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.port or
            parsed.path or parsed.query or parsed.fragment):
        return None
    return parsed.scheme + "://" + parsed.hostname


def _policy_allows_worker(policy: str, origin: str) -> bool:
    directives = [part.strip().split() for part in policy.split(";")]
    images = [part[1:] for part in directives if part and part[0] == "img-src"]
    # No wildcard, substring match, or permissive default-src fallback.
    return len(images) == 1 and origin in images[0]


def _live_csp_allows_worker(app_url: str, origin: str) -> bool:
    app_origin = _valid_https_origin(app_url)
    if app_origin is None:
        return False
    request = Request(app_origin + "/etsy-spy", method="HEAD")
    try:
        with urlopen(request, timeout=5) as response:
            headers = response.headers
    except HTTPError as exc:
        # CAM nginx sends CSP "always", including to unauthorized responses.
        headers = exc.headers
    except (URLError, OSError, ValueError):
        return False
    policies = headers.get_all("Content-Security-Policy", []) if headers else []
    # Multiple CSP headers apply cumulatively in browsers.
    return bool(policies) and all(
        _policy_allows_worker(policy, origin) for policy in policies
    )


def probe(values: dict[str, str], *, do_network: bool) -> dict[str, object]:
    required = (
        "R2_ENDPOINT", "R2_BUCKET_NAME", "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY", "R2_VIDEO_MEDIA_BASE_URL",
        "R2_VIDEO_MEDIA_SIGNING_SECRET",
    )
    missing = [key for key in required if not values.get(key, "").strip()]
    allowlist = {x.strip() for x in
        values.get("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", "").split(",")
        if x.strip()}
    canary = "boostgame" in allowlist
    report = {
        "reuses_existing_bucket": True,
        "missing_existing_keys": missing,
        "shared_r2_cache_enabled": values.get(
            "CAM_ETSY_IMAGE_SHARED_R2_ENABLED", "").lower() in {"1", "true"},
        "boostgame_canary_present": canary,
        "edge_redirect_enabled": values.get(
            "CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED", "").lower() in {"1", "true"},
        "worker_signed_head": "not_run",
        "csp_allows_worker": None,
        "edge_ready": False,
    }
    if missing or not do_network:
        return report
    origin = _valid_https_origin(values["R2_VIDEO_MEDIA_BASE_URL"])
    if not origin:
        report["worker_signed_head"] = "invalid_origin"
        report["csp_allows_worker"] = False
        return report
    report["csp_allows_worker"] = _live_csp_allows_worker(
        values.get("PUBLIC_APP_URL", ""), origin
    )
    secret = values["R2_VIDEO_MEDIA_SIGNING_SECRET"]
    tenant_tag = hmac.new(secret.encode(), b"etsy-image-tenant-v1\0__cam_image_cdn_probe__",
                          hashlib.sha256).hexdigest()[:24]
    object_key = ("etsy-images/v1/" + tenant_tag + "/sp-" + "0" * 40 +
                  "/01/thumbnail/" + "0" * 64 + ".png")
    path = "/" + object_key
    exp = int(time.time()) + 45
    message = f"v1\netsy-image-read\n{path}\n{exp}".encode("utf-8")
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    signed = origin + path + f"?v=1&exp={exp}&sig={signature}"
    try:
        with urlopen(Request(signed, method="HEAD"), timeout=7) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except (URLError, OSError, ValueError):
        status = 0
    report["worker_signed_head"] = {
        404: "signed_image_route_verified",
        403: "existing_worker_still_video_only_or_signature_rejected",
        503: "worker_or_binding_unavailable",
        0: "network_unavailable",
    }.get(status, "unexpected_response")
    report["edge_ready"] = (
        report["shared_r2_cache_enabled"] and canary and
        report["csp_allows_worker"] is True and status == 404
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default="/etc/creative-asset-manager/production.env")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        values = parse_environment_file(Path(args.env_file))
        report = probe(values, do_network=args.probe)
        print(json.dumps(report, sort_keys=True))
        return 0 if (report["edge_ready"] if args.probe
                     else not report["missing_existing_keys"]) else 2
    except (SafeConfigurationError, FileNotFoundError):
        print('{"error":"configuration_unavailable","edge_ready":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
