#!/usr/bin/env python3
"""Load a root-owned production EnvironmentFile without evaluating shell code."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_BASE_ENV = ("PATH", "LANG", "LC_ALL", "TZ")


class SafeConfigurationError(ValueError):
    """An error whose message never contains an environment value."""


def parse_environment_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SafeConfigurationError("Environment file cannot be read.") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            raise SafeConfigurationError(
                f"Invalid environment syntax at line {line_number}."
            )
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _KEY_RE.fullmatch(key) or key in values:
            raise SafeConfigurationError(
                f"Invalid or duplicate environment key at line {line_number}."
            )
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            try:
                parsed = shlex.split(value, comments=False, posix=True)
            except ValueError as exc:
                raise SafeConfigurationError(
                    f"Invalid quoted value at line {line_number}."
                ) from exc
            if len(parsed) != 1:
                raise SafeConfigurationError(
                    f"Invalid quoted value at line {line_number}."
                )
            value = parsed[0]
        if "\x00" in value:
            raise SafeConfigurationError(
                f"Invalid environment value at line {line_number}."
            )
        values[key] = value
    return values


def validate_file_security(path: Path, expected_owner_uid: int) -> None:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise SafeConfigurationError("Environment file metadata is unavailable.") from exc
    if metadata.st_uid != expected_owner_uid:
        raise SafeConfigurationError("Environment file owner is invalid.")
    forbidden = stat.S_IWGRP | stat.S_IRWXO
    if metadata.st_mode & forbidden:
        raise SafeConfigurationError("Environment file permissions are too broad.")


def reject_placeholders(values: Mapping[str, str]) -> None:
    keys = sorted(key for key, value in values.items() if "REPLACE_" in value)
    if keys:
        raise SafeConfigurationError(
            "Replacement placeholders remain in settings: " + ", ".join(keys)
        )


def validate_application_settings(values: Mapping[str, str], api_root: Path) -> None:
    if not (api_root / "app" / "core" / "config.py").is_file():
        raise SafeConfigurationError("API root does not contain application settings.")
    previous_environment = dict(os.environ)
    previous_path = list(sys.path)
    try:
        os.environ.clear()
        os.environ.update(values)
        sys.path.insert(0, str(api_root))
        from app.core.config import Settings

        try:
            settings = Settings()
        except Exception as exc:
            errors = getattr(exc, "errors", None)
            if callable(errors):
                safe_errors = errors(include_url=False, include_input=False)
                locations = []
                for error in safe_errors[:10]:
                    location = ".".join(str(item) for item in error.get("loc", ()))
                    error_type = str(error.get("type", "invalid"))
                    locations.append(f"{location or 'settings'} ({error_type})")
                suffix = ", ".join(locations) or type(exc).__name__
                raise SafeConfigurationError(
                    "Production settings are invalid: " + suffix
                ) from exc
            raise SafeConfigurationError(
                f"Production settings validation failed ({type(exc).__name__})."
            ) from exc
        if not settings.is_production:
            raise SafeConfigurationError("APP_ENV must select production mode.")
    finally:
        os.environ.clear()
        os.environ.update(previous_environment)
        sys.path[:] = previous_path


def child_environment(values: Mapping[str, str]) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in _SAFE_BASE_ENV
        if key in os.environ
    }
    environment.update(values)
    return environment


def checked_values(args: argparse.Namespace) -> dict[str, str]:
    path = Path(args.env_file).resolve(strict=True)
    validate_file_security(path, args.expected_owner_uid)
    values = parse_environment_file(path)
    reject_placeholders(values)
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(dest="action", required=True)

    def common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--env-file", required=True)
        subparser.add_argument("--expected-owner-uid", type=int, required=True)

    check = subparsers.add_parser("check")
    common(check)
    check.add_argument("--api-root", required=True)

    flag = subparsers.add_parser("flag-enabled")
    common(flag)
    flag.add_argument("--name", required=True)
    visible = subparsers.add_parser("run-redacted")
    common(visible)
    visible.add_argument("command", nargs=argparse.REMAINDER)


    run = subparsers.add_parser("run-quiet")
    common(run)
    run.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        values = checked_values(args)
        if args.action == "check":
            validate_application_settings(values, Path(args.api_root).resolve())
            print("Production configuration is valid.")
            return 0
        if args.action == "flag-enabled":
            if not _KEY_RE.fullmatch(args.name):
                raise SafeConfigurationError("Feature flag name is invalid.")
            return 0 if values.get(args.name, "").strip().lower() == "true" else 1
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise SafeConfigurationError("No command was provided.")
        result = subprocess.run(
            command,
            env=child_environment(values),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
        output = result.stdout
        for value in sorted(values.values(), key=len, reverse=True):
            if len(value) >= 4:
                output = output.replace(value, "[redacted]")
        if args.action == "run-redacted" and output:
            print(output, end="" if output.endswith("\n") else "\n")
        if result.returncode:
            print("ERROR: production command failed.", file=sys.stderr)
            if args.action == "run-quiet" and output.strip():
                print(output[-4000:], file=sys.stderr)
        return result.returncode
    except SafeConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"ERROR: production environment operation failed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
