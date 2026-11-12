"""Isolated, bounded Codex CLI image+text runner for Etsy Picked listing drafts.

Uses the service account's existing ChatGPT login; no separately billed API
fallback. Never runs model-provided commands, publishes a listing or persists
the source photo beyond the job. Output is untrusted until tiktok_skill QA.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import tempfile
import time
from typing import Callable


MAX_OUTPUT_BYTES = 65536
MAX_STDERR_TAIL_BYTES = 8192


class CodexListingUnavailable(RuntimeError):
    def __init__(self, code: str, retry_seconds: int = 120):
        super().__init__(code)
        self.code = code
        self.retry_seconds = retry_seconds


class CodexListingCancelled(RuntimeError):
    pass


def _diagnose_failure(tail: str) -> CodexListingUnavailable:
    message = tail.casefold()
    if any(value in message for value in (
        "usage limit", "quota", "rate limit", "too many requests",
        "usage cap", "resource_exhausted", "credits exhausted",
    )):
        return CodexListingUnavailable("tiktok_codex_quota_wait", 1800)
    if any(value in message for value in (
        "not logged in", "authentication required", "unauthorized",
        "sign in to", "please log in", "invalid credentials",
    )):
        return CodexListingUnavailable("tiktok_codex_auth_wait", 3600)
    if any(value in message for value in (
        "connection reset", "connection refused", "timed out",
        "service unavailable", "network error", "error sending request",
    )):
        return CodexListingUnavailable("tiktok_codex_network_wait", 900)
    return CodexListingUnavailable("tiktok_codex_execution_failed", 180)


def _stderr_tail(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - MAX_STDERR_TAIL_BYTES), os.SEEK_SET)
        return stream.read(MAX_STDERR_TAIL_BYTES).decode("utf8", "replace")


async def generate_codex_listing(
    *,
    photo: bytes,
    mime: str,
    prompt: str,
    schema: dict,
    codex_home: str,
    timeout_seconds: int = 240,
    model: str = "",
    binary: str = "codex",
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Return one JSON model response. Do not expose raw provider logs or image."""
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError("unsupported_codex_photo_type")
    if not isinstance(photo, bytes) or not photo or len(photo) > 8_000_000:
        raise ValueError("invalid_codex_photo")
    if not 30 <= timeout_seconds <= 600:
        raise ValueError("invalid_codex_timeout")
    if not codex_home or not Path(codex_home).is_absolute():
        raise ValueError("invalid_codex_home")

    # Serialise this low-volume manual job across all image workers. A full
    # Codex account outage must not stampede concurrent subprocesses.
    lock_file = Path(codex_home) / ".tiktok-listing-codex.lock"
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise CodexListingUnavailable("tiktok_codex_runtime_unavailable", 600) from exc
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CodexListingUnavailable("tiktok_codex_busy", 45) from exc
        with tempfile.TemporaryDirectory(prefix="cam-tiktok-listing-codex-") as tmp:
            root = Path(tmp)
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
            image = root / ("source" + ext)
            image.write_bytes(photo)
            schema_path, output_path = root / "schema.json", root / "draft.json"
            schema_path.write_text(json.dumps(schema), encoding="utf8")
            args = [
                binary, "exec", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "read-only", "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
                "--image", str(image), "-C", str(root),
            ]
            if model:
                if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}", model):
                    raise ValueError("invalid_codex_model")
                args.extend(["--model", model])
            args.append(prompt)
            environment = dict(os.environ)
            for key in (
                "OPENAI_API_KEY", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
                "OPENAI_BASE_URL", "CODEX_API_KEY", "OPENAI_EXECUTOR_API_KEY",
            ):
                environment.pop(key, None)
            environment["CODEX_HOME"] = codex_home
            environment["HOME"] = codex_home

            stdout_path, stderr_path = root / "stdout.log", root / "stderr.log"
            with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                proc = await asyncio.create_subprocess_exec(
                    *args, stdin=asyncio.subprocess.DEVNULL,
                    stdout=stdout_stream, stderr=stderr_stream,
                    env=environment, start_new_session=True,
                )
                deadline = time.monotonic() + timeout_seconds
                try:
                    while proc.returncode is None:
                        if cancelled is not None and cancelled():
                            raise CodexListingCancelled()
                        if time.monotonic() >= deadline:
                            raise TimeoutError("tiktok_codex_timeout")
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                except BaseException:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await proc.wait()
                    raise
            if proc.returncode != 0:
                raise _diagnose_failure(_stderr_tail(stderr_path))
            if not output_path.is_file() or output_path.stat().st_size > MAX_OUTPUT_BYTES:
                raise CodexListingUnavailable("tiktok_codex_output_missing_or_oversized")
            try:
                result = json.loads(output_path.read_text(encoding="utf8"))
            except (ValueError, UnicodeError) as exc:
                raise CodexListingUnavailable("tiktok_codex_invalid_json") from exc
            if not isinstance(result, dict):
                raise CodexListingUnavailable("tiktok_codex_invalid_json")
            return result
    finally:
        os.close(lock_fd)
