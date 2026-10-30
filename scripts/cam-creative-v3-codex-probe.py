#!/usr/bin/env python3
"""One-shot Codex CLI built-in ImageGen capability probe. No Images API usage.

Default --check-only is safe/non-generating. Explicit --native uses one Codex
subscription turn to request ONE native PNG. Never runs against production
Stage 1 jobs, never edits product assets, never enables feature flags.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time


FORBIDDEN_ENV = (
    "OPENAI_API_KEY", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
    "OPENAI_BASE_URL", "CODEX_API_KEY", "OPENAI_EXECUTOR_API_KEY",
)
PNG_HEADER = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 30 * 1024 * 1024


def safe_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in FORBIDDEN_ENV:
        env.pop(key, None)
    env["CODEX_HOME"] = str(codex_home)
    return env


def run(command: list[str], *, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, env=env, capture_output=True, text=True, check=False,
        timeout=timeout, stdin=subprocess.DEVNULL,
    )


def png_info(path: Path) -> dict | None:
    if path.is_symlink() or not path.is_file() or not 60 <= path.stat().st_size <= MAX_PNG_BYTES:
        return None
    with path.open("rb") as stream:
        header = stream.read(33)
    if (len(header) < 33 or header[:8] != PNG_HEADER
            or header[12:16] != b"IHDR"):
        return None
    width, height, bit_depth, color_type = struct.unpack(">IIBB", header[16:26])
    if not (512 <= width <= 4096 and 512 <= height <= 4096
            and bit_depth == 8 and color_type in (4, 6)):
        return None
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "width": width,
            "height": height, "bytes": len(data)}


def tool_call_events(events: str) -> int:
    count = 0
    for raw in events.splitlines():
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") not in (
                "item.started", "item.completed", "item.updated"):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") not in (
                "tool_call", "mcp_tool_call", "dynamic_tool_call", "image_generation"):
            continue
        label = " ".join(str(item.get(k) or "") for k in ("tool_name", "name", "server", "type"))
        if re.search(r"image[_ -]?(?:gen|generation)", label, re.I):
            count += 1
    return count


def probe(args: argparse.Namespace) -> dict:
    home = args.codex_home.expanduser().resolve()
    binary = shutil.which(args.binary)
    result = {
        "provider": "codex_subscription_cli", "api_fallback_allowed": False,
        "native_requested": bool(args.native),
        "imagegen_available": False,
        "status": "unverified",
    }
    if not binary or not home.is_dir():
        return {**result, "status": "codex_binary_or_home_missing"}
    env = safe_env(home)
    version = run([binary, "--version"], env=env, timeout=12)
    result["cli_version"] = version.stdout.strip()[:70]
    auth = run([binary, "login", "status"], env=env, timeout=20)
    # Do not print auth tokens, profile paths or raw Codex config.
    result["chatgpt_login"] = ("logged in using chatgpt" in
                               (auth.stdout + auth.stderr).casefold())
    if not result["chatgpt_login"]:
        return {**result, "status": "chatgpt_login_required"}
    features = run([binary, "features", "list"], env=env, timeout=20)
    result["image_feature_enabled"] = any(
        line.split()[-1].lower() == "true"
        for line in features.stdout.splitlines()
        if line.strip().startswith("image_generation ")
    )
    if not result["image_feature_enabled"]:
        return {**result, "status": "codex_image_feature_disabled"}
    if not args.native:
        return {**result, "status": "ready_for_native_probe_not_verified"}

    cache = home / "generated_images"
    existing = set(cache.iterdir()) if cache.is_dir() and not cache.is_symlink() else set()
    with tempfile.TemporaryDirectory(prefix="cam-v3-codex-probe-") as temp:
        work = Path(temp)
        (work / "output").mkdir()
        prompt = (
            "Use the REAL built-in $imagegen tool once to create ONE original "
            "transparent satin-stitched embroidery artwork, exact text "
            "CREATIVE DICTATOR, no mockup or added words. Copy the unmodified "
            "native PNG to output/probe.png. If the built-in image_gen tool is "
            "not available, report that and create NO output. "
            "NEVER use the paid Images API, scripts/image_gen.py, an API key, "
            "an image editor, SVG, programmatic drawing, or a substitute."
        )
        command = [
            binary, "exec", "--json", "--ephemeral",
            "--skip-git-repo-check", "-s", "workspace-write", "-C", str(work),
        ]
        if args.model:
            command.extend(["-m", args.model])
        command.append(prompt)
        started = time.monotonic()
        try:
            attempt = run(command, env=env, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            return {**result, "status": "native_probe_timed_out", "max_seconds": args.timeout}
        result["elapsed_seconds"] = round(time.monotonic() - started, 1)
        result["codex_exit_code"] = attempt.returncode
        result["image_tool_events"] = tool_call_events(attempt.stdout)
        final = work / "output" / "probe.png"
        info = png_info(final)
        if not info:
            result["status"] = "native_tool_missing_or_no_png"
            return result
        result["png_size"] = [info["width"], info["height"]]
        # Never trust a model-created PNG as genuine ImageGen without
        # byte-identical provenance in the job's native generated-images cache.
        found = False
        if cache.is_dir() and not cache.is_symlink():
            for file in cache.iterdir():
                if file in existing or file.is_symlink() or not file.is_file():
                    continue
                native = png_info(file)
                if native and native["sha256"] == info["sha256"]:
                    found = True
                    break
        if not found:
            result["status"] = "native_png_provenance_unverified"
            return result
        result["status"] = "native_imagegen_verified"
        result["imagegen_available"] = True
        result["native_sha256_prefix"] = info["sha256"][:12]
        if args.keep_output:
            dest_dir = args.output_dir.expanduser().resolve()
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / "codex-v3-native-probe.png"
            with dest.open("xb") as stream:
                stream.write(final.read_bytes())
            result["saved_output"] = str(dest)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", "~/.codex")))
    parser.add_argument("--binary", default="codex")
    parser.add_argument("--native", action="store_true",
                        help="Explicitly try ONE subscription-authenticated built-in image call.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--model", default="")
    parser.add_argument("--keep-output", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    if not 30 <= args.timeout <= 300:
        parser.error("--timeout must be between 30 and 300 seconds")
    if args.keep_output and not args.native:
        parser.error("--keep-output requires --native")
    try:
        status = probe(args)
    except (OSError, subprocess.TimeoutExpired) as exc:
        status = {"provider": "codex_subscription_cli", "status": type(exc).__name__,
                  "imagegen_available": False, "api_fallback_allowed": False}
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return 0 if status.get("status") in (
        "ready_for_native_probe_not_verified", "native_imagegen_verified") else 2


if __name__ == "__main__":
    raise SystemExit(main())
