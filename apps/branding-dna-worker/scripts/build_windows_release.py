#!/usr/bin/env python3
"""Build and verify a deterministic Windows bootstrap ZIP (not a signed EXE).

Includes only explicit release files. No secrets, browser binary, normal
Chrome profile, node_modules, local reports or platform API credentials.
Run on any Python 3 host; install Chromium separately on dedicated Windows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / "apps" / "branding-dna-worker"
VERSION = "0.2.0"
BASENAME = f"BrandingDNA-BrowserAgent-v{VERSION}-win-x64"
STAMP = (2026, 10, 16, 0, 0, 0)

PACKAGE_FILES = (
    "README.md",
    "QUICK_START_VI.txt",
    "package.json",
    "package-lock.json",
    "INSTALL_TOOL.cmd",
    "CHECK_TOOL.cmd",
    "START_BROWSER_PILOT.cmd",
    "START_BRANDING_DNA_WORKER.cmd",
    "config/pilot.example.json",
    "config/approval.example.json",
    "src/browser_pilot.mjs",
    "src/run_configured_pilot.mjs",
    "src/check_setup.mjs",
    "src/heartbeat.mjs",
    "src/outbox.mjs",
    "tests/browser_pilot.test.mjs",
    "tests/configured_pilot.test.mjs",
    "tests/heartbeat.test.mjs",
    "tests/outbox.test.mjs",
)
DOC_PATH = ROOT / "docs/features/branding-dna/BROWSER_AGENT_PILOT.md"


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def release_inputs() -> dict[str, bytes]:
    package = json.loads((WORKER / "package.json").read_text("utf8"))
    lock = json.loads((WORKER / "package-lock.json").read_text("utf8"))
    if package["version"] != VERSION or lock["version"] != VERSION:
        raise ValueError("version_mismatch_package_lock")
    if lock["packages"][""]["version"] != VERSION:
        raise ValueError("version_mismatch_lock_root")
    if package["dependencies"].get("playwright") != "1.56.1":
        raise ValueError("playwright_must_remain_pinned")

    files = {}
    for rel in PACKAGE_FILES:
        path = WORKER / rel
        if not path.is_file() or path.is_symlink():
            raise ValueError("release_missing_or_untrusted_file: " + rel)
        blob = path.read_bytes()
        if rel.endswith(".cmd"):
            # Windows command prompt expects native newlines.
            blob = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        files[rel] = blob
    if not DOC_PATH.is_file() or DOC_PATH.is_symlink():
        raise ValueError("release_pilot_document_missing")
    files["docs/BROWSER_AGENT_PILOT.md"] = DOC_PATH.read_bytes()
    return files


def manifest(files: dict[str, bytes]) -> dict:
    return {
        "name": BASENAME,
        "version": VERSION,
        "format": "windows_portable_bootstrap",
        "binary_bundled": False,
        "node_bundled": False,
        "marketplace_api_used": False,
        "external_scraping_service_used": False,
        "production_scan_enabled": False,
        "automatic_scan_on_launch": False,
        "file_count": len(files),
        "files": {name: {"sha256": sha(blob), "bytes": len(blob)}
                  for name, blob in sorted(files.items())},
    }


def _member(name: str, content: bytes, archive: zipfile.ZipFile):
    info = zipfile.ZipInfo(f"{BASENAME}/{name}", date_time=STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    archive.writestr(info, content, compresslevel=9)


def build(dest: Path | None = None) -> Path:
    files = release_inputs()
    report = manifest(files)
    if dest is None:
        dest = ROOT / "releases" / "branding-dna" / (BASENAME + ".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w") as archive:
        for name, blob in sorted(files.items()):
            _member(name, blob, archive)
        _member("MANIFEST.json", (json.dumps(report, sort_keys=True, indent=2) + "\n").encode(), archive)
    verify(dest)
    digest = sha(dest.read_bytes())
    dest.with_suffix(dest.suffix + ".sha256").write_text(
        digest + "  " + dest.name + "\n", encoding="ascii"
    )
    return dest


def verify(dest: Path) -> dict:
    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        prefix = BASENAME + "/"
        if len(names) != len(set(names)) or any(
            not n.startswith(prefix) or
            any(p in ("", ".", "..") for p in PurePosixPath(n).parts)
            for n in names
        ):
            raise ValueError("unsafe_archive_paths")
        man = prefix + "MANIFEST.json"
        if man not in names:
            raise ValueError("manifest_missing")
        metadata = json.loads(archive.read(man))
        if metadata.get("name") != BASENAME or metadata.get("production_scan_enabled") is not False:
            raise ValueError("invalid_manifest")
        expected = set(metadata["files"].keys())
        actual = {n.removeprefix(prefix) for n in names if n != man}
        if expected != actual or metadata.get("file_count") != len(expected):
            raise ValueError("archive_members_mismatch")
        for rel in expected:
            info = metadata["files"][rel]
            data = archive.read(prefix + rel)
            if sha(data) != info["sha256"] or len(data) != info["bytes"]:
                raise ValueError("archive_file_hash_mismatch:" + rel)
        if "config/pilot.local.json" in expected or "config/approval.local.json" in expected:
            raise ValueError("private_config_in_archive")
        return metadata


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--verify", type=Path, help="Verify an existing release archive")
    args = cli.parse_args()
    if args.verify:
        result = verify(args.verify)
        print(f"VERIFIED {result['name']} ({result['file_count']} source files)")
    else:
        filename = build()
        print(f"BUILT {filename}")
        print(f"SHA256 {sha(filename.read_bytes())}")
        print("Note: Windows Node.js and Chromium are installed separately by INSTALL_TOOL.cmd.")
