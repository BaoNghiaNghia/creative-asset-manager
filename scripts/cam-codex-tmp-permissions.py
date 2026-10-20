#!/usr/bin/env python3
"""Read-only Codex temp dir owner/permission audit; no file contents."""
from __future__ import annotations
import collections
import os
from pathlib import Path
import pwd
import stat
import time

homes = (
    Path("/var/lib/creative-asset-manager/codex"),
    Path("/var/lib/creative-asset-manager/codex-account-2"),
)
user = pwd.getpwnam("creative-assets")
gid = user.pw_gid
groups = set(os.getgrouplist(user.pw_name, gid))
def can_write_dir(st):
    if st.st_uid == user.pw_uid:
        return bool(st.st_mode & stat.S_IWUSR and st.st_mode & stat.S_IXUSR)
    if st.st_gid in groups:
        return bool(st.st_mode & stat.S_IWGRP and st.st_mode & stat.S_IXGRP)
    return bool(st.st_mode & stat.S_IWOTH and st.st_mode & stat.S_IXOTH)

for home in homes:
    print("\nCODEX HOME", home)
    print("Target service user:", user.pw_name, "uid", user.pw_uid, "gid", gid)
    tmp = home / "tmp"
    if not tmp.is_dir():
        print("No tmp directory"); continue
    owners = collections.Counter()
    blocked = collections.Counter()
    blocked_paths = []
    modes = collections.Counter()
    files = collections.Counter()
    directories = collections.Counter()
    age = collections.Counter()
    allocated_dirs = 0
    errors = 0
    stack = [tmp]
    while stack:
        path = stack.pop()
        try:
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode):
                continue
            owners[st.st_uid] += 1
            modes[oct(stat.S_IMODE(st.st_mode))] += 1
            directories[st.st_uid] += 1
            allocated_dirs += st.st_blocks*512
            if not can_write_dir(st):
                blocked[(st.st_uid, oct(stat.S_IMODE(st.st_mode)))] += 1
                if len(blocked_paths) < 4:
                    blocked_paths.append(str(path.relative_to(tmp)))
            for child in os.scandir(path):
                try:
                    cst = child.stat(follow_symlinks=False)
                    if stat.S_ISDIR(cst.st_mode):
                        stack.append(Path(child.path))
                    elif stat.S_ISREG(cst.st_mode):
                        files[(cst.st_uid, oct(stat.S_IMODE(cst.st_mode)), cst.st_size == 0)] += 1
                        days = (time.time() - cst.st_mtime) / 86400
                        age["<1d" if days < 1 else "1-3d" if days < 3 else "3d+"] += 1
                except OSError:
                    errors += 1
        except OSError:
            errors += 1
    print("directory owners UID/count:", owners.most_common(8))
    print("directory permission modes:", modes.most_common(8))
    print("directories NOT writable by creative-assets:", sum(blocked.values()), "top UID/mode:", blocked.most_common(8))
    print("unwritable relative dir samples:", blocked_paths)
    print("files by UID/mode/zero-length:", files.most_common(8))
    print("file age counts:", age)
    print("directory allocated MB:", round(allocated_dirs / 1048576, 1))
    print("unreadable:", errors)
print("\nAudit only: no permissions, files or credentials were modified.")
