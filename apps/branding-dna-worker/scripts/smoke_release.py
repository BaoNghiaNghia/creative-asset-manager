#!/usr/bin/env python3
"""Smoke-test the contents of a built portable ZIP without visiting a shop."""
from pathlib import Path
import subprocess
import tempfile
import zipfile

from build_windows_release import BASENAME, ROOT, build, verify


def smoke() -> None:
    release = ROOT / "releases/branding-dna" / (BASENAME + ".zip")
    if not release.is_file():
        build(release)
    verify(release)
    with tempfile.TemporaryDirectory(prefix="bdna-release-qa-") as workdir:
        with zipfile.ZipFile(release) as archive:
            archive.extractall(workdir)
        root = Path(workdir) / BASENAME
        subprocess.run(
            ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=root, check=True, timeout=120,
        )
        subprocess.run(["npm", "test"], cwd=root, check=True, timeout=120)
        subprocess.run(
            ["node", "--check", "src/run_configured_pilot.mjs"],
            cwd=root, check=True, timeout=30,
        )
        print("RELEASE SMOKE OK: npm ci + tests from extracted portable ZIP")
        print("Note: this smoke test does not run Windows CMD or launch Chromium.")


if __name__ == "__main__":
    smoke()
