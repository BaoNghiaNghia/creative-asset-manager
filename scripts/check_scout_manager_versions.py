#!/usr/bin/env python3
"""Validate Scout Manager release manifests without modifying Cargo.lock.

Pin libredox's dependency version deliberately; do not bulk-replace every
occurrence of the application's version in the lockfile.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps/scout-manager-desktop"


def main() -> None:
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    node_lock = json.loads((APP / "package-lock.json").read_text(encoding="utf-8"))
    config = json.loads((APP / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((APP / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((APP / "src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    version = package["version"]
    assert (node_lock["version"] == node_lock["packages"][""]["version"] ==
            cargo["package"]["version"] == config["version"] == version), "Scout Manager version mismatch"
    own = [p for p in lock["package"] if p["name"] == "cam-scout-manager"]
    assert len(own) == 1 and own[0]["version"] == version, "Scout Manager Cargo lock entry mismatch"
    redox = [p for p in lock["package"] if p["name"] == "libredox"]
    # Update this deliberate pin only after cargo update resolves the new
    # actual dependency and its checksum. This caught an accidental 0.1.26
    # global textual replacement during Scout Manager 0.1.26 packaging.
    assert len(redox) == 1 and redox[0]["version"] == "0.1.25", "libredox lockfile accidentally modified"
    assert len(redox[0]["checksum"]) == 64, "Invalid pinned dependency checksum"
    print(f"Scout Manager manifest lockfile preflight passed: {version}")


if __name__ == "__main__":
    main()
