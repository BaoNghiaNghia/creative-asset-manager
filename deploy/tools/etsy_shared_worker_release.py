#!/usr/bin/env python3
"""Guarded deployment of Etsy image reads on CAM's EXISTING video Worker.

Never creates another Worker or R2 bucket. Never reads/prints Cloudflare API
credentials. The Etsy browser redirect flag must stay OFF until a signed
image HEAD returns 404 on the deployed Worker and CSP permits its origin.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .etsy_shared_r2_preflight import probe
from .production_env import SafeConfigurationError, parse_environment_file
from .r2_video_worker_rollout import RolloutConfigError, validate_config

EXPECTED_WORKER = "cam-r2-original-video"
EXPECTED_BUCKET = "creative-asset-video-cache"
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = REPO_ROOT / "infrastructure/cloudflare/r2-video-worker"
PRODUCTION_CONFIG = WORKER_DIR / "wrangler.production.json"
PRODUCTION_ENV = Path("/etc/creative-asset-manager/production.env")


class ReleaseBlocked(Exception):
    pass


def gate(values: dict[str, str], config: dict) -> dict[str, object]:
    """Validate static safety invariants without contacting the network."""
    target = validate_config(config)
    if target["worker_name"] != EXPECTED_WORKER:
        raise ReleaseBlocked("unexpected_worker_name")
    if target["bucket_name"] != EXPECTED_BUCKET or values.get("R2_BUCKET_NAME") != EXPECTED_BUCKET:
        raise ReleaseBlocked("unexpected_existing_bucket")
    if not target["workers_dev"] or target["endpoint_mode"] != "workers_dev":
        raise ReleaseBlocked("existing_worker_endpoint_mode_mismatch")
    url = urlsplit(values.get("R2_VIDEO_MEDIA_BASE_URL", "").rstrip("/"))
    if (url.scheme != "https" or not url.hostname or
            not url.hostname.startswith(EXPECTED_WORKER + ".") or
            not url.hostname.endswith(".workers.dev") or
            url.port or url.path or url.query or url.fragment or
            url.username or url.password):
        raise ReleaseBlocked("existing_worker_origin_mismatch")
    if values.get("R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV", "").lower() not in ("true", "1"):
        raise ReleaseBlocked("workers_dev_not_allowed_by_backend")
    video_ttl = int(values.get("R2_VIDEO_MEDIA_TICKET_TTL_SECONDS", "600"))
    if not 1 <= video_ttl <= int(target["max_ttl_seconds"]):
        raise ReleaseBlocked("worker_ttl_shorter_than_video_tickets")
    if values.get("CAM_ETSY_IMAGE_SHARED_R2_ENABLED", "").lower() not in ("true", "1"):
        raise ReleaseBlocked("shared_r2_not_enabled")
    canaries = {t.strip() for t in
                values.get("CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS", "").split(",")
                if t.strip()}
    if canaries != {"boostgame"}:
        raise ReleaseBlocked("canary_scope_not_exact_boostgame")
    if values.get("CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED", "").lower() in ("true", "1"):
        raise ReleaseBlocked("edge_flag_must_be_disabled_during_worker_deploy")
    return {
        "worker": target["worker_name"],
        "bucket": target["bucket_name"],
        "endpoint": "existing_workers_dev",
        "video_ticket_seconds": video_ttl,
        "worker_max_ttl_seconds": target["max_ttl_seconds"],
        "canary": "boostgame",
        "edge_flag": "off",
    }


def probe_video_head(values: dict[str, str]) -> int:
    """One synthetic, signed, read-only video HEAD against the live Worker.

    A valid ticket for a nonexistent video object must return 404. No user
    assets or secrets are logged or sent to an untrusted origin.
    """
    origin = values["R2_VIDEO_MEDIA_BASE_URL"].rstrip("/")
    path = "/video-cache/boostgame/" + "0" * 64 + "/original"
    expires = int(time.time()) + 30
    message = f"v1\nread\n{path}\n{expires}".encode("ascii")
    mac = hmac.new(values["R2_VIDEO_MEDIA_SIGNING_SECRET"].encode("utf-8"),
                   message, hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")
    signed = origin + path + f"?v=1&exp={expires}&sig={signature}"
    try:
        with urlopen(Request(signed, method="HEAD"), timeout=6) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except (URLError, OSError, ValueError):
        return 0
    return status


def run(args: list[str], *, capture: bool = True) -> bool:
    """No untrusted shell interpolation and no credential or URL printing."""
    result = subprocess.run(
        args, cwd=WORKER_DIR, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True, check=False, timeout=120,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "deploy"))
    parser.add_argument("--env-file", type=Path, default=PRODUCTION_ENV)
    parser.add_argument("--config", type=Path, default=PRODUCTION_CONFIG)
    args = parser.parse_args()
    try:
        values = parse_environment_file(args.env_file)
        config = json.loads(args.config.read_text(encoding="utf-8"))
        target = gate(values, config)
        edge = probe(values, do_network=True)
        target["csp_allows_worker"] = edge["csp_allows_worker"]
        target["signed_image_head"] = edge["worker_signed_head"]
        video_status = probe_video_head(values)
        target["signed_video_head"] = ("verified" if video_status == 404 else "status_" + str(video_status))
        if video_status != 404:
            raise ReleaseBlocked("existing_video_worker_probe_failed_status_" + str(video_status))
        target["edge_ready_before_deploy"] = edge["edge_ready"]
        if edge["csp_allows_worker"] is not True:
            raise ReleaseBlocked("browser_csp_not_ready")
        if edge["worker_signed_head"] not in (
            "signed_image_route_verified",
            "existing_worker_still_video_only_or_signature_rejected",
        ):
            raise ReleaseBlocked("existing_worker_not_responsive")
        if args.action == "check":
            print(json.dumps({"release_gate": "ready_for_worker_auth", **target}, sort_keys=True))
            return 0

        wrangler = str(WORKER_DIR / "node_modules/.bin/wrangler")
        if not Path(wrangler).is_file():
            raise ReleaseBlocked("local_wrangler_not_installed")
        if not run([wrangler, "whoami"]):
            raise ReleaseBlocked("cloudflare_auth_unavailable")
        config_arg = str(args.config.resolve())
        # A successful remote list is required: never silently create a new
        # Worker when the existing service name/binding is incorrect.
        if not run([wrangler, "deployments", "list", "--config", config_arg]):
            raise ReleaseBlocked("existing_worker_cannot_be_verified")
        for command, message in (
            (["npm", "test"], "worker_tests_failed"),
            (["npm", "run", "typecheck"], "worker_typecheck_failed"),
            ([wrangler, "deploy", "--dry-run", "--strict", "--keep-vars",
              "--config", config_arg], "worker_dry_run_failed"),
        ):
            if not run(command):
                raise ReleaseBlocked(message)
        if edge["edge_ready"]:
            print(json.dumps({"release_gate": "already_ready_no_worker_mutation", **target},
                             sort_keys=True))
            return 0
        # Only the existing Worker/bucket may be changed. --keep-vars avoids
        # removing unrelated remote dashboard variables; --strict rejects drift.
        if not run([wrangler, "deploy", "--strict", "--keep-vars",
                    "--config", config_arg]):
            raise ReleaseBlocked("worker_deploy_failed")
        for attempt in range(3):
            checked = probe(values, do_network=True)
            if checked["edge_ready"] and probe_video_head(values) == 404:
                print(json.dumps({
                    "release_gate": "worker_deployed_signed_image_route_verified",
                    "worker": EXPECTED_WORKER, "bucket": EXPECTED_BUCKET,
                    "canary": "boostgame", "edge_flag": "off",
                }, sort_keys=True))
                return 0
            if attempt != 2:
                time.sleep(2)
        # Fail closed: browser redirect remains OFF. Attempt to restore the
        # prior live version on a failed post-deploy verification.
        rolled_back = run([wrangler, "rollback", "--yes", "--config", config_arg])
        raise ReleaseBlocked("post_deploy_probe_failed_rollback_" +
                             ("succeeded" if rolled_back else "requires_operator"))
    except (ReleaseBlocked, RolloutConfigError, SafeConfigurationError,
            OSError, ValueError, KeyError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        if isinstance(exc, ReleaseBlocked):
            reason = str(exc)
        else:
            reason = "invalid_release_configuration"
        print(json.dumps({"release_gate": "blocked", "reason": reason}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
