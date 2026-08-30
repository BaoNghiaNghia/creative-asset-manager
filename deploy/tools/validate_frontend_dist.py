#!/usr/bin/env python3
"""Reject production frontend artifacts that contain endpoint or credential values.

Sensitive field names are valid UI/API contract text and are not secrets by
themselves. This validator rejects known credential shapes and literal
assignments of sensitive fields while keeping diagnostics redacted.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_STATIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("localhost endpoint", re.compile(r"https?://localhost(?::[0-9]+)?", re.I)),
    ("loopback endpoint", re.compile(r"127[.]0[.]0[.]1")),
    ("database environment marker", re.compile(r"(?<![A-Za-z0-9_])DATABASE_URL(?![A-Za-z0-9_])")),
    ("PostgreSQL URI", re.compile(r"postgres(?:ql)?://", re.I)),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{20,}")),
    ("OpenAI API key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("Google OAuth access token", re.compile(r"ya29[.][0-9A-Za-z_-]{20,}")),
    ("Google OAuth refresh token", re.compile(r"1//[0-9A-Za-z_-]{20,}")),
    ("Google OAuth client secret", re.compile(r"GOCSPX-[0-9A-Za-z_-]{16,}")),
    ("private key", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY", re.I)),
)

_SENSITIVE_LITERAL = re.compile(
    r"""(?ix)
    ["']?(refresh_token|access_token|client_secret)["']?
    \s*[:=]\s*
    (["'])
    [0-9a-z._~+/\-=]{16,}
    \2
    """
)


def violations(dist: Path) -> list[tuple[str, str]]:
    if not dist.is_dir():
        raise ValueError("frontend dist directory does not exist")
    found: list[tuple[str, str]] = []
    for path in sorted(item for item in dist.rglob("*") if item.is_file()):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = str(path.relative_to(dist))
        for label, pattern in _STATIC_RULES:
            if pattern.search(text):
                found.append((relative, label))
        if _SENSITIVE_LITERAL.search(text):
            found.append((relative, "literal OAuth credential"))
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate_frontend_dist.py DIST", file=sys.stderr)
        return 2
    try:
        found = violations(Path(argv[1]))
    except ValueError as exc:
        print(f"Frontend dist validation failed: {exc}", file=sys.stderr)
        return 2
    if not found:
        return 0
    for path, label in found:
        print(f"Forbidden frontend artifact content: {path} ({label})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
