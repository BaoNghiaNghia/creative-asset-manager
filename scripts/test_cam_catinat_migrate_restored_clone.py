"""Safeguard tests: no live database access or secrets."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("catinat_clone_only", HERE / "cam_catinat_migrate_restored_clone.py")
assert spec and spec.loader
safeguard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = safeguard
spec.loader.exec_module(safeguard)


class FakeRunner:
    def __init__(self, *, original_revision=safeguard.SOURCE_REVISION,
                 count_change=False, identity=None, restore_tables=207):
        self.original_revision = original_revision
        self.count_change = count_change
        self.restore_tables = restore_tables
        self.identity = identity or "|".join([
            safeguard.DATABASE, safeguard.HOST, str(safeguard.PORT), safeguard.ROLE, "f",
        ])
        self.calls = []
        self.migrated = False

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] == "psql":
            sql = argv[-1]
            if "current_database()" in sql:
                output = self.identity
            elif "version_num" in sql:
                output = safeguard.TARGET_REVISION if self.migrated else self.original_revision
            elif "COUNT(*)" in sql:
                if "c.relname IN" in sql:
                    output = str(len(safeguard.EXPECTED_TABLES) if self.migrated else 0)
                elif "pg_class" in sql:
                    output = str(self.restore_tables + (len(safeguard.EXPECTED_TABLES) if self.migrated else 0))
                else:
                    output = str(6 if self.migrated and self.count_change else 5)
            else:
                raise AssertionError(f"Unexpected query {sql}")
        else:
            assert argv[1:3] == ["-m", "alembic"]
            assert argv[-2:] == ["upgrade", safeguard.TARGET_REVISION]
            assert kwargs["env"]["DATABASE_URL"] == (
                f"postgresql+psycopg://{safeguard.ROLE}@{safeguard.HOST}:{safeguard.PORT}/{safeguard.DATABASE}"
            )
            self.migrated = True
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)


@pytest.fixture
def inputs(tmp_path):
    work = tmp_path / "private"
    work.mkdir(mode=0o700)
    work.chmod(0o700)
    evidence = work / "restore-proof.json"
    evidence.write_text(json.dumps({
        "RESTORE_REHEARSAL": "PASS",
        "REHEARSAL_DATABASE_VALIDATED": True,
        "RESTORE_TARGET": safeguard.DATABASE,
        "TARGET_HOST": safeguard.HOST,
        "BACKUP_SHA256": safeguard.MANAGED_BACKUP_SHA256,
        "RESTORED_TABLES": 207,
    }))
    evidence.chmod(0o600)
    passfile = work / "rehearsal.pgpass"
    passfile.write_text(
        f"{safeguard.HOST}:{safeguard.PORT}:{safeguard.DATABASE}:{safeguard.ROLE}:test_secret_not_real\n"
    )
    passfile.chmod(0o600)
    checkout = tmp_path / "code"
    (checkout / "apps/api").mkdir(parents=True)
    (checkout / "database/migrations/versions").mkdir(parents=True)
    (checkout / "apps/api/alembic.ini").write_text("[alembic]\n")
    (checkout / "database/migrations/versions/0172_catinat_transfers.py").write_text("#mock")
    (checkout / "database/migrations/versions/0173_merge_inventory_branding_heads.py").write_text("#mock")
    outfile = work / "migration-proof.json"
    return dict(evidence=evidence, pgpassfile=passfile, outfile=outfile,
                checkout=checkout, python_path=Path(sys.executable), rehearsal_dir=work)


def test_clone_only_migration_checks_before_after_and_produces_safe_proof(inputs):
    fake = FakeRunner()
    report = safeguard.run_clone_migration(**inputs, runner=fake)
    assert report["ISOLATED_MIGRATION"] == "PASS"
    assert report["SOURCE_REVISION"] == safeguard.SOURCE_REVISION
    assert report["TARGET_REVISION"] == safeguard.TARGET_REVISION
    assert report["CREATED_CATINAT_TABLES"] == 6
    assert report["PRODUCTION_MIGRATION"] == "NOT_RUN"
    assert json.loads(inputs["outfile"].read_text()) == report
    assert (inputs["outfile"].stat().st_mode & 0o777) == 0o600
    assert len([args for args, _ in fake.calls if args[0] != "psql"]) == 1
    for args, kwargs in fake.calls:
        assert "5432" not in args
        assert kwargs["env"]["PGPASSFILE"] == str(inputs["pgpassfile"])
        assert "PGPASSWORD" not in kwargs["env"]
        assert "test_secret_not_real" not in repr(args) + repr(kwargs)


def test_stale_revision_never_runs_migration(inputs):
    fake = FakeRunner(original_revision="0167_unexpected")
    with pytest.raises(safeguard.CloneMigrationBlocked, match="source revision"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.migrated
    assert not inputs["outfile"].exists()


def test_wrong_database_identity_blocks_all_writes(inputs):
    fake = FakeRunner(identity=f"production|127.0.0.1|5432|postgres|t")
    with pytest.raises(safeguard.CloneMigrationBlocked, match="identity"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.migrated


def test_source_backup_hash_mismatch_blocks_without_network(inputs):
    contents = json.loads(inputs["evidence"].read_text())
    contents["BACKUP_SHA256"] = "f" * 64
    inputs["evidence"].write_text(json.dumps(contents))
    fake = FakeRunner()
    with pytest.raises(safeguard.CloneMigrationBlocked, match="verified backup"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.calls


def test_existing_migration_proof_never_overwritten(inputs):
    inputs["outfile"].write_text("operator evidence")
    fake = FakeRunner()
    with pytest.raises(safeguard.CloneMigrationBlocked, match="new file"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.calls
    assert inputs["outfile"].read_text() == "operator evidence"


def test_password_file_target_must_match_isolated_database(inputs):
    inputs["pgpassfile"].write_text("localhost:5432:production:postgres:secret\n")
    fake = FakeRunner()
    with pytest.raises(safeguard.CloneMigrationBlocked, match="solely"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.calls


def test_invalid_private_permissions_stop_before_any_db_call(inputs):
    inputs["evidence"].chmod(0o644)
    fake = FakeRunner()
    with pytest.raises(safeguard.CloneMigrationBlocked, match="0600"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.calls


def test_no_proof_if_source_rows_change(inputs):
    fake = FakeRunner(count_change=True)
    with pytest.raises(safeguard.CloneMigrationBlocked, match="source rows"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert fake.migrated
    assert not inputs["outfile"].exists()


def test_pg_service_redirection_stripped_from_migration_environment(inputs,monkeypatch):
    monkeypatch.setenv("PGSERVICE", "production")
    monkeypatch.setenv("PGHOSTADDR", "198.51.100.7")
    monkeypatch.setenv("PGPASSWORD", "some_password")
    monkeypatch.setenv("DATABASE_URL", "postgresql://production.invalid/secret")
    fake = FakeRunner()
    safeguard.run_clone_migration(**inputs, runner=fake)
    for _args,kwargs in fake.calls:
        assert "PGSERVICE" not in kwargs["env"]
        assert "PGHOSTADDR" not in kwargs["env"]
        assert "PGPASSWORD" not in kwargs["env"]
        assert "production.invalid" not in repr(kwargs["env"])


def test_already_migrated_database_not_reused(inputs):
    fake = FakeRunner(original_revision=safeguard.TARGET_REVISION)
    with pytest.raises(safeguard.CloneMigrationBlocked, match="source revision"):
        safeguard.run_clone_migration(**inputs, runner=fake)
    assert not fake.migrated
