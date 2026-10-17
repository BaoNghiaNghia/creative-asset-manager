"""Deterministic, complete and non-overlapping unittest sharding for GitHub CI.

Run from apps/api so test discovery and app imports use the same environment
as the existing API job.  Keep all non-integration tests; errors from unittest
discovery are real test failures, not silently skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import unittest
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.core.test_bootstrap import activate_test_environment


def flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def select(tests, shard: int, total: int):
    if not 0 <= shard < total:
        raise ValueError("shard index must be within the total shard count")
    return [
        test for test in tests
        if int.from_bytes(hashlib.sha256(test.id().encode("utf-8")).digest()[:8], "big") % total == shard
    ]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--total", type=int, required=True)
    args = parser.parse_args(argv)
    if not 0 <= args.index < args.total <= 16:
        parser.error("expected 0 <= index < total <= 16")
    os.chdir(API_ROOT)
    activate_test_environment()
    discovered = unittest.defaultTestLoader.discover("tests")
    candidates = [
        test for test in flatten(discovered)
        if not any(segment in {"integration", "migrations"} for segment in test.id().split("."))
    ]
    # Discovery can import the same unittest method more than once. Keep its
    # first occurrence so every stable test ID is owned by exactly one shard.
    candidates = list({test.id(): test for test in candidates}.values())
    selected = select(candidates, args.index, args.total)
    print(
        f"CI_UNIT_SHARD={args.index}/{args.total}; "
        f"SELECTED={len(selected)}; TOTAL_DISCOVERED={len(candidates)}",
        flush=True,
    )
    if not selected:
        raise SystemExit("empty shard; refusing to report success")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
