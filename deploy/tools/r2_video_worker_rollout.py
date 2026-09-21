"""Render and inspect a secret-free production config for the R2 video Worker.

This tool never reads, accepts, stores, or uploads secret values. Remote-changing
Wrangler commands are emitted as an operator plan only; they are never executed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_WORKER_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,62}")
SECRET_NAME = "R2_VIDEO_MEDIA_SIGNING_SECRET"
BINDING_NAME = "VIDEO_CACHE_BUCKET"


class RolloutConfigError(ValueError):
    pass


def _media_host(value: str) -> str:
    host = value.strip().casefold().rstrip(".")
    if not _HOST.fullmatch(host):
        raise RolloutConfigError("media host must be a DNS hostname without scheme or path")
    if (
        host == "r2.dev"
        or host == "workers.dev"
        or host.endswith(".r2.dev")
        or host.endswith(".workers.dev")
        or host == "r2.cloudflarestorage.com"
        or host.endswith(".r2.cloudflarestorage.com")
    ):
        raise RolloutConfigError(
            "custom-domain mode does not allow workers.dev, r2.dev, or raw R2 hosts"
        )
    return host


def _worker_name(value: str) -> str:
    name = value.strip().casefold()
    if not _WORKER_NAME.fullmatch(name):
        raise RolloutConfigError("worker name is invalid")
    return name


def _bucket_name(value: str) -> str:
    name = value.strip()
    if name != name.casefold() or not _BUCKET.fullmatch(name):
        raise RolloutConfigError("R2 bucket name is invalid")
    return name


def build_config(
    *,
    worker_name: str,
    bucket_name: str,
    media_host: str | None = None,
    workers_dev: bool = False,
    max_ttl_seconds: int = 3600,
) -> dict[str, Any]:
    if isinstance(max_ttl_seconds, bool) or not 1 <= max_ttl_seconds <= 3600:
        raise RolloutConfigError("max TTL must be between 1 and 3600 seconds")
    if workers_dev and media_host:
        raise RolloutConfigError("--workers-dev and --media-host are mutually exclusive")
    if not workers_dev and not media_host:
        raise RolloutConfigError("either --media-host or --workers-dev is required")

    config: dict[str, Any] = {
        "$schema": "./node_modules/wrangler/config-schema.json",
        "name": _worker_name(worker_name),
        "main": "src/index.ts",
        "compatibility_date": "2026-09-20",
        "workers_dev": bool(workers_dev),
        "r2_buckets": [
            {
                "binding": BINDING_NAME,
                "bucket_name": _bucket_name(bucket_name),
            }
        ],
        "vars": {
            "R2_VIDEO_MEDIA_MAX_TTL_SECONDS": str(max_ttl_seconds),
        },
        "secrets": {
            "required": [SECRET_NAME],
        },
    }
    if not workers_dev:
        config["routes"] = [
            {
                "pattern": _media_host(str(media_host)),
                "custom_domain": True,
            }
        ]
    return config


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    try:
        worker_name = _worker_name(str(config["name"]))
        if config.get("main") != "src/index.ts":
            raise RolloutConfigError("Worker main configuration is unsafe")

        workers_dev = config.get("workers_dev")
        if not isinstance(workers_dev, bool):
            raise RolloutConfigError("workers_dev must be explicitly true or false")

        media_host: str | None = None
        if workers_dev:
            routes = config.get("routes")
            if routes not in (None, []):
                raise RolloutConfigError("workers.dev mode must not declare a custom-domain route")
            endpoint_mode = "workers_dev"
        else:
            routes = config.get("routes")
            if not isinstance(routes, list) or len(routes) != 1:
                raise RolloutConfigError("exactly one custom domain is required")
            route = routes[0]
            if route.get("custom_domain") is not True:
                raise RolloutConfigError("Worker route must be a Custom Domain")
            media_host = _media_host(str(route["pattern"]))
            endpoint_mode = "custom_domain"

        buckets = config["r2_buckets"]
        if not isinstance(buckets, list) or len(buckets) != 1:
            raise RolloutConfigError("exactly one R2 binding is required")
        binding = buckets[0]
        if binding.get("binding") != BINDING_NAME:
            raise RolloutConfigError("unexpected R2 binding name")
        bucket_name = _bucket_name(str(binding["bucket_name"]))
        required = config.get("secrets", {}).get("required")
        if required != [SECRET_NAME]:
            raise RolloutConfigError("required signing secret declaration is missing")
        ttl = int(config.get("vars", {}).get("R2_VIDEO_MEDIA_MAX_TTL_SECONDS", "0"))
        if not 1 <= ttl <= 3600:
            raise RolloutConfigError("Worker max TTL is invalid")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RolloutConfigError):
            raise
        raise RolloutConfigError("Worker production config is incomplete") from exc
    return {
        "worker_name": worker_name,
        "endpoint_mode": endpoint_mode,
        "media_host": media_host,
        "bucket_name": bucket_name,
        "max_ttl_seconds": ttl,
        "workers_dev": workers_dev,
        "required_secret_names": [SECRET_NAME],
    }


def rollout_plan(
    config_path: Path,
    *,
    release_tag: str,
    allow_workers_dev: bool = False,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", release_tag):
        raise RolloutConfigError("release tag is invalid")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    summary = validate_config(config)
    if summary["workers_dev"] and not allow_workers_dev:
        raise RolloutConfigError(
            "workers.dev rollout requires explicit --allow-workers-dev operator opt-in"
        )
    config_arg = str(config_path)
    steps: list[dict[str, Any]] = [
        {
            "stage": "local_verify",
            "mutates_remote": False,
            "commands": ["npm ci", "npm test", "npm run typecheck"],
        },
        {
            "stage": "wrangler_dry_run",
            "mutates_remote": False,
            "commands": [
                f"npx wrangler deploy --dry-run --config {config_arg}"
            ],
        },
        {
            "stage": "remote_inspect",
            "mutates_remote": False,
            "commands": [
                f"npx wrangler deployments list --config {config_arg}"
            ],
            "note": (
                "Stop if the expected Worker project does not already exist; "
                "bootstrap it only under separate operator approval."
            ),
        },
        {
            "stage": "operator_secret_setup",
            "mutates_remote": True,
            "commands": [
                f"npx wrangler versions secret put {SECRET_NAME} --config {config_arg}"
            ],
            "note": (
                "Enter the value only through Wrangler's protected prompt; "
                "never pass it as a CLI argument."
            ),
        },
        {
            "stage": "upload_version",
            "mutates_remote": True,
            "commands": [
                f"npx wrangler versions upload --config {config_arg} --tag {release_tag}"
            ],
            "note": "Uploads a version without enabling application CDN delivery.",
        },
    ]
    if not summary["workers_dev"]:
        steps.append(
            {
                "stage": "route_review",
                "mutates_remote": False,
                "commands": [
                    f"npx wrangler triggers deploy --dry-run --config {config_arg}"
                ],
            }
        )
    steps.extend(
        [
            {
                "stage": "deployment",
                "mutates_remote": True,
                "commands": [
                    f"npx wrangler versions deploy --config {config_arg}"
                ],
                "note": (
                    "Keep the persisted application runtime toggle OFF until "
                    "the signed Worker probes and activation gate are green."
                ),
            },
            {
                "stage": "rollback",
                "mutates_remote": True,
                "commands": [
                    f"npx wrangler rollback --config {config_arg}"
                ],
            },
        ]
    )
    return {
        "config": summary,
        "secret_values_in_plan": False,
        "application_runtime_must_remain_off": True,
        "steps": steps,
    }


def _write_config(path: Path, config: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise RolloutConfigError("output config already exists; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Secret-free R2 video Worker production rollout helper"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render a validated secret-free Wrangler config")
    render.add_argument("--worker-name", required=True)
    render.add_argument("--bucket-name", required=True)
    endpoint = render.add_mutually_exclusive_group(required=True)
    endpoint.add_argument("--media-host")
    endpoint.add_argument(
        "--workers-dev",
        action="store_true",
        help="publish through the account workers.dev subdomain instead of a Custom Domain",
    )
    render.add_argument("--max-ttl-seconds", type=int, default=3600)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--force", action="store_true")

    plan = sub.add_parser("plan", help="print a non-executing rollout plan")
    plan.add_argument("--config", type=Path, required=True)
    plan.add_argument("--release-tag", required=True)
    plan.add_argument(
        "--allow-workers-dev",
        action="store_true",
        help="explicitly approve a workers.dev delivery rollout for this plan",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            config = build_config(
                worker_name=args.worker_name,
                bucket_name=args.bucket_name,
                media_host=args.media_host,
                workers_dev=args.workers_dev,
                max_ttl_seconds=args.max_ttl_seconds,
            )
            _write_config(args.output, config, force=args.force)
            print(json.dumps({
                "output": str(args.output),
                "secret_values_written": False,
                "config": validate_config(config),
            }, sort_keys=True))
            return 0
        result = rollout_plan(
            args.config,
            release_tag=args.release_tag,
            allow_workers_dev=args.allow_workers_dev,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RolloutConfigError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
