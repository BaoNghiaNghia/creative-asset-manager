"""Inert Phase 4C.3F: bounded, descriptor-pinned immutable file I/O.

Never resolve paths supplied by clients. Every directory and file is opened
relative to a trusted dir fd with O_NOFOLLOW; the exact descriptor read for
hash verification is the one whose bytes are returned to a future viewer.
The allowed namespaces have *separate* content and identity semantics.
No HTTP route, queue worker or dispatcher imports this module.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
from contextlib import ExitStack
from pathlib import Path

MAX_FENCED_BLOB_BYTES = 22_000_000
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_FORMATS = {
    "fenced-v1": ".png",
    "fenced-source-v1": ".bin",
}
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _valid_metadata(*, namespace: str, key: str, digest: str, size: int) -> bool:
    return bool(
        isinstance(namespace, str) and namespace in _FORMATS
        and isinstance(key, str)
        and _HEX64.fullmatch(key) and isinstance(digest, str)
        and _HEX64.fullmatch(digest) and type(size) is int
        and 0 < size <= MAX_FENCED_BLOB_BYTES
        and (namespace != "fenced-source-v1" or key == digest)
    )


def _root_fd(root: Path) -> int:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError("trusted_absolute_storage_root_required")
    # Trust that the admin-provisioned ancestors of root are not writable by
    # untrusted workers; O_NOFOLLOW pins the configured root leaf itself.
    return os.open(root, _DIRECTORY_FLAGS)


def _child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        os.fsync(parent_fd)  # directory entry must survive a machine crash
    return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def _read_leaf(directory_fd: int, name: str, *, size: int, sha256: str) -> bytes | None:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    except (OSError, ValueError):
        return None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size:
            return None
        # Never stream directly to HTTP before the digest has passed.
        raw = bytearray()
        digest = hashlib.sha256()
        while len(raw) <= size:
            part = os.read(fd, min(65536, size + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
            digest.update(part)
        return bytes(raw) if len(raw) == size and digest.hexdigest() == sha256 else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def read_fenced_blob_pinned(
    storage_root: Path, *, namespace: str,
    key: str, expected_sha256: str, expected_bytes: int,
) -> bytes | None:
    """Return only verified bytes; reject symlinks on EVERY path component.

    Return None on any unsafe/missing/mismatched disk state. This is a low
    level library, NOT an auth decision; callers must independently validate
    their authenticated tenant, DB job, receipt and review-only status.
    """
    if not _valid_metadata(
        namespace=namespace, key=key, digest=expected_sha256,
        size=expected_bytes,
    ):
        return None
    try:
        with ExitStack() as stack:
            root_fd = _root_fd(storage_root)
            stack.callback(os.close, root_fd)
            namespace_fd = _child_directory(root_fd, namespace, create=False)
            stack.callback(os.close, namespace_fd)
            shard_fd = _child_directory(namespace_fd, key[:2], create=False)
            stack.callback(os.close, shard_fd)
            return _read_leaf(
                shard_fd, key + _FORMATS[namespace],
                size=expected_bytes, sha256=expected_sha256,
            )
    except (OSError, ValueError):
        return None


def store_fenced_blob_once(
    storage_root: Path, *, namespace: str,
    key: str, expected_sha256: str, raw: bytes,
) -> bool:
    """Fsync new dirs/file and hard-link atomically without replacing data.

    A crashed writer can leave an unreferenced temp file; a verified existing
    final name is reusable. Callers must still commit their receipt after this
    returns true. This is not a distributed filesystem/database transaction.
    """
    if (
        not isinstance(raw, bytes)
        or not _valid_metadata(
            namespace=namespace, key=key, digest=expected_sha256, size=len(raw),
        )
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        return False
    try:
        with ExitStack() as stack:
            root_fd = _root_fd(storage_root)
            stack.callback(os.close, root_fd)
            namespace_fd = _child_directory(root_fd, namespace, create=True)
            stack.callback(os.close, namespace_fd)
            shard_fd = _child_directory(namespace_fd, key[:2], create=True)
            stack.callback(os.close, shard_fd)
            target = key + _FORMATS[namespace]
            if _read_leaf(shard_fd, target, size=len(raw), sha256=expected_sha256) == raw:
                os.fsync(shard_fd)
                return True
            # The name may already exist with different bytes or as a symlink.
            try:
                os.stat(target, dir_fd=shard_fd, follow_symlinks=False)
                return False
            except FileNotFoundError:
                pass
            temp_name = ".pending-" + secrets.token_hex(16)
            fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600, dir_fd=shard_fd,
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(
                        temp_name, target,
                        src_dir_fd=shard_fd, dst_dir_fd=shard_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    pass
                result = (
                    _read_leaf(shard_fd, target, size=len(raw), sha256=expected_sha256) == raw
                )
                os.fsync(shard_fd)
                return result
            finally:
                os.unlink(temp_name, dir_fd=shard_fd)
                os.fsync(shard_fd)
    except (OSError, ValueError):
        return False
