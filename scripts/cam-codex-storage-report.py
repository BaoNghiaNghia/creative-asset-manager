#!/usr/bin/env python3
"""Read-only Codex homes metadata audit. Never prints auth tokens or file contents."""
from __future__ import annotations
import collections
import datetime as dt
import os
import pathlib
import stat
import time

ROOTS = (
    pathlib.Path("/var/lib/creative-asset-manager/codex"),
    pathlib.Path("/var/lib/creative-asset-manager/codex-account-2"),
)
NOW = time.time()
classify = lambda name: (
    "sessions" if name in {"sessions", "archived_sessions"} else
    "auth/config" if name in {"auth.json", "config.toml", "managed_config.toml", "credentials.json", "token.json"} else
    "logs" if name in {"log", "logs", "history.jsonl"} else
    "cache" if name in {"cache", ".cache", "tmp", "temp", "state", ".npm", ".local", "node_modules"} else
    "skills" if name == "skills" else
    "generated/output" if name in {"smoke-workspace", "output", "artifacts", "images"} else
    "other"
)
def human(value: int) -> str:
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or suffix == "TiB":
            return f"{value:.1f}{suffix}"
        value /= 1024
    return str(value)

for root in ROOTS:
    print(f"\n=== CODEX HOME {root} ===")
    if not root.is_dir():
        print("MISSING"); continue
    print("Symlinks not followed. Counts based on allocated disk blocks, not virtual file size.")
    buckets = collections.defaultdict(lambda: [0, 0, 0, 0])
    ext = collections.defaultdict(lambda: [0, 0])
    recent = collections.defaultdict(lambda: [0, 0])
    age_buckets = collections.defaultdict(lambda: [0, 0])
    large = []
    errors = 0
    stack = [root]
    while stack:
        base = stack.pop()
        try:
            entries = list(os.scandir(base))
        except OSError:
            errors += 1
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                rel = pathlib.Path(entry.path).relative_to(root).parts
                group = rel[0]
                allocated = st.st_blocks * 512
                if stat.S_ISDIR(st.st_mode):
                    buckets[group][2] += 1
                    buckets[group][0] += allocated
                    stack.append(pathlib.Path(entry.path))
                elif stat.S_ISREG(st.st_mode):
                    buckets[group][0] += allocated
                    buckets[group][1] += 1
                    buckets[group][3] += st.st_size
                    extension = pathlib.Path(entry.name).suffix.lower() or "[none]"
                    ext[(group,extension)][0] += allocated
                    ext[(group,extension)][1] += 1
                    age = (NOW - st.st_mtime) / 86400
                    age_label = ("<1d" if age < 1 else "1-3d" if age < 3 else
                                 "3-7d" if age < 7 else "7-14d" if age < 14 else
                                 "14-30d" if age < 30 else "30d+")
                    age_buckets[(group, age_label)][0] += allocated
                    age_buckets[(group, age_label)][1] += 1
                    if age < 1: recent[group][0] += allocated; recent[group][1] += 1
                    large.append((allocated, group, extension, age))
            except OSError:
                errors += 1
    total = sum(v[0] for v in buckets.values())
    print(f"Allocated total: {human(total)}, groups={len(buckets)}, unreadable={errors}")
    print("TOP LEVEL: group / type / allocated / files / directories / logical bytes")
    for group, vals in sorted(buckets.items(), key=lambda kv: kv[1][0], reverse=True):
        print(f"  {group:28} {classify(group):16} {human(vals[0]):>10} {vals[1]:>8} files {vals[2]:>6} dirs logical {human(vals[3])}")
    print("FILE TYPES > 10MiB (top 15):")
    for (grp,suffix), vals in sorted(ext.items(), key=lambda kv: kv[1][0], reverse=True)[:15]:
        print(f"  {grp:27} {suffix:10} {human(vals[0]):>10} in {vals[1]} files")
    print("WRITTEN LAST 24 HOURS:")
    for group, (size, count) in sorted(recent.items(), key=lambda kv: kv[1][0], reverse=True):
        if size: print(f"  {group:28} {human(size):>10}, {count} files")
    print("PNG AGE / GENERATED_IMAGES (age of filesystem file, not Drive proof):")
    for span in ("<1d", "1-3d", "3-7d", "7-14d", "14-30d", "30d+"):
        val = age_buckets[("generated_images", span)]
        print(f"  {span:>7}: {human(val[0]):>10}, {val[1]} files")
    print("LARGEST INDIVIDUAL FILES (names and contents intentionally hidden):")
    for size,group,extension,age in sorted(large, reverse=True)[:12]:
        print(f"  {human(size):>10} | group={group} | ext={extension} | age_days={max(0,age):.1f}")
print("\nNo files were read or changed; auth contents and session messages were never opened.")
