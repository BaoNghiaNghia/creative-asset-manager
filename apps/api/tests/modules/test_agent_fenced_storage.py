"""No-follow, hash-bound, atomic content-addressed I/O regression suite."""
from __future__ import annotations

import hashlib

from app.modules.agent_harness.fenced_storage import (
    read_fenced_blob_pinned, store_fenced_blob_once,
)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def test_pinned_read_and_idempotent_source_store(tmp_path):
    raw = b"independently fetched source test fixture"
    digest = _digest(raw)
    kwargs = dict(
        namespace="fenced-source-v1", key=digest,
        expected_sha256=digest,
    )
    assert store_fenced_blob_once(tmp_path, **kwargs, raw=raw)
    assert store_fenced_blob_once(tmp_path, **kwargs, raw=raw)
    assert read_fenced_blob_pinned(
        tmp_path, **kwargs, expected_bytes=len(raw),
    ) == raw
    target = tmp_path / "fenced-source-v1" / digest[:2] / (digest + ".bin")
    assert target.read_bytes() == raw
    assert list(target.parent.iterdir()) == [target]
    assert not store_fenced_blob_once(
        tmp_path, **kwargs, raw=b"not the same digest",
    )
    assert target.read_bytes() == raw


def test_receipt_id_is_not_output_digest_and_cannot_be_edited_in_place(tmp_path):
    raw = b"PNG bytes are separately validated by fenced_output"
    digest = _digest(raw)
    receipt = "a" * 64
    assert receipt != digest
    assert store_fenced_blob_once(
        tmp_path, namespace="fenced-v1",
        key=receipt, expected_sha256=digest, raw=raw,
    )
    assert read_fenced_blob_pinned(
        tmp_path, namespace="fenced-v1",
        key=receipt, expected_sha256=digest, expected_bytes=len(raw),
    ) == raw
    file = tmp_path / "fenced-v1" / receipt[:2] / (receipt + ".png")
    file.write_bytes(b"mutated")
    assert read_fenced_blob_pinned(
        tmp_path, namespace="fenced-v1",
        key=receipt, expected_sha256=digest, expected_bytes=len(raw),
    ) is None
    assert not store_fenced_blob_once(
        tmp_path, namespace="fenced-v1",
        key=receipt, expected_sha256=digest, raw=raw,
    )
    assert file.read_bytes() == b"mutated"


def test_symlink_root_namespace_shard_and_final_file_fail_closed(tmp_path):
    raw = b"test safe file descriptor"
    digest = _digest(raw)
    kwargs = dict(
        namespace="fenced-source-v1", key=digest,
        expected_sha256=digest,
    )
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "symlink"
    link.symlink_to(target, target_is_directory=True)
    assert not store_fenced_blob_once(link, **kwargs, raw=raw)
    assert read_fenced_blob_pinned(
        link, **kwargs, expected_bytes=len(raw),
    ) is None
    assert store_fenced_blob_once(target, **kwargs, raw=raw)
    namespace = target / "fenced-source-v1"
    namespace.rename(target / "moved")
    namespace.symlink_to(target / "moved", target_is_directory=True)
    assert read_fenced_blob_pinned(
        target, **kwargs, expected_bytes=len(raw),
    ) is None
    assert not store_fenced_blob_once(target, **kwargs, raw=raw)
    namespace.unlink()
    (target / "moved").rename(namespace)
    shard = namespace / digest[:2]
    shard.rename(namespace / "other")
    shard.symlink_to(namespace / "other", target_is_directory=True)
    assert read_fenced_blob_pinned(
        target, **kwargs, expected_bytes=len(raw),
    ) is None
    shard.unlink()
    (namespace / "other").rename(shard)
    file = shard / (digest + ".bin")
    file.unlink()
    file.symlink_to(tmp_path / "escape")
    assert read_fenced_blob_pinned(
        target, **kwargs, expected_bytes=len(raw),
    ) is None
    assert not store_fenced_blob_once(target, **kwargs, raw=raw)


def test_reject_invalid_metadata_and_non_dir_root(tmp_path):
    raw = b"small"
    digest = _digest(raw)
    assert not store_fenced_blob_once(
        tmp_path, namespace="other", key=digest,
        expected_sha256=digest, raw=raw,
    )
    assert not store_fenced_blob_once(
        tmp_path, namespace="fenced-source-v1",
        key="a" * 64, expected_sha256=digest, raw=raw,
    )
    for size in (0, -1, True, 22_000_001):
        assert read_fenced_blob_pinned(
            tmp_path, namespace="fenced-source-v1",
            key=digest, expected_sha256=digest,
            expected_bytes=size,
        ) is None
    missing = tmp_path / "does-not-exist"
    assert not store_fenced_blob_once(
        missing, namespace="fenced-source-v1",
        key=digest, expected_sha256=digest, raw=raw,
    )
