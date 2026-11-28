"""Input gate tests; fixtures are not actual backup or G1 evidence."""
from pathlib import Path
import json
import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p2d_migrate_restored_clone as p


def fixture():
    snapshot = {
        "backup_sha256": "a"*64, "backup_md5": "b"*32,
        "source_revision": p.SOURCE, "row_counts": {name: 0 for name in p.PROTECTED},
    }
    restore = {
        "RESTORE_REHEARSAL": "PASS", "FILE_MATCHES_EXPECTED_CHECKSUMS": True,
        "REHEARSAL_DATABASE_VALIDATED": True, "RESTORE_TARGET": p.DB,
        "TARGET_HOST": p.HOST, "EXPECTED_TABLE_COUNTS_MATCHED": True,
        "REQUIRED_TABLE_COUNTS_VERIFIED": True,
        "REQUIRED_TABLE_COUNT": len(p.PROTECTED),
        "BACKUP_SHA256": snapshot["backup_sha256"], "BACKUP_MD5": snapshot["backup_md5"],
        "RESTORED_TABLES": 230,
    }
    return restore, snapshot


def test_valid_metadata():
    r, s = fixture()
    assert p.validate_metadata(r, s) == s["row_counts"]


@pytest.mark.parametrize("key,value", [
    ("RESTORE_REHEARSAL", "FAIL"), ("RESTORE_TARGET", "creative_asset_manager"),
    ("TARGET_HOST", "192.0.2.1"), ("FILE_MATCHES_EXPECTED_CHECKSUMS", False),
    ("EXPECTED_TABLE_COUNTS_MATCHED", False), ("REQUIRED_TABLE_COUNT", 0),
    ("BACKUP_SHA256", "f"*64), ("RESTORED_TABLES", True),
])
def test_bad_restore(key, value):
    r, s = fixture()
    r[key] = value
    with pytest.raises(p.Blocked):
        p.validate_metadata(r, s)


def test_bad_snapshot():
    r, s = fixture()
    del s["row_counts"]["cam_muse_jobs"]
    with pytest.raises(p.Blocked):
        p.validate_metadata(r, s)
    r, s = fixture()
    s["row_counts"]["cam_muse_jobs"] = True
    with pytest.raises(p.Blocked):
        p.validate_metadata(r, s)
    r, s = fixture()
    s["source_revision"] = p.TARGET
    with pytest.raises(p.Blocked):
        p.validate_metadata(r, s)


def test_private_file_scope(tmp_path):
    tmp_path.chmod(0o700)
    r, s = fixture()
    for name, value in [
        ("restore-proof.json", json.dumps(r)),
        ("source-snapshot.json", json.dumps(s)),
        ("rehearsal.pgpass", f"{p.HOST}:{p.PORT}:{p.DB}:{p.ROLE}:fixture-only"),
    ]:
        f = tmp_path / name
        f.write_text(value)
        f.chmod(0o600)
    assert p.load_inputs(tmp_path)[1] == s["row_counts"]
    (tmp_path/"p2d-migration-proof.json").touch()
    with pytest.raises(p.Blocked, match="proof_already_exists"):
        p.load_inputs(tmp_path)
    (tmp_path/"p2d-migration-proof.json").unlink()
    f = tmp_path/"rehearsal.pgpass"
    f.write_text("127.0.0.1:5432:creative_asset_manager:cam_app:fixture")
    with pytest.raises(p.Blocked, match="isolated_pgpass_required"):
        p.load_inputs(tmp_path)
    f.chmod(0o644)
    with pytest.raises(p.Blocked, match="private_file_required"):
        p.load_inputs(tmp_path)
