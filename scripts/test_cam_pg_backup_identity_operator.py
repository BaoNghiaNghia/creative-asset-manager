from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.engine import URL

SCRIPT = Path(__file__).with_name("cam_pg_backup_identity_operator.py")
SPEC = importlib.util.spec_from_file_location("cam_pg_backup_identity_operator", SCRIPT)
assert SPEC and SPEC.loader
operator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operator)


def target():
    return URL.create(
        "postgresql+psycopg", username="cam_app", password=operator.secrets.token_urlsafe(24),
        host="127.0.0.1", port=5432, database="creative_asset_manager",
    )


def test_load_target_accepts_fixed_app_scope_and_never_echoes_credentials(tmp_path):
    prod = tmp_path / "production.env"
    secret = operator.secrets.token_urlsafe(24)
    value = target().set(password=secret).render_as_string(hide_password=False)
    prod.write_text("DATABASE_URL=" + value + "\n", encoding="utf-8")
    prod.chmod(0o640)
    parsed = operator.load_target(prod)
    assert parsed.host == "127.0.0.1"
    assert parsed.username == "cam_app"
    assert parsed.password == secret
    for invalid in (
        target().set(database="other_database"),
        target().set(username="postgres"),
        URL.create("sqlite", database="creative_asset_manager"),
        target().set(query={"host": "external"}),
    ):
        prod.write_text("DATABASE_URL=" + invalid.render_as_string(hide_password=False))
        with pytest.raises(operator.BackupIdentityGateError) as error:
            operator.load_target(prod)
        assert secret not in str(error.value)


def test_ddl_only_grants_read_to_new_role():
    random_secret = operator.secrets.token_urlsafe(24)
    ddl = operator.ddl_for_role(random_secret)
    assert "BEGIN;" in ddl and "COMMIT;" in ddl
    assert "CREATE ROLE cam_backup WITH LOGIN" in ddl
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in ddl
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO cam_backup;" in ddl
    assert "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO cam_backup;" in ddl
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES" not in ddl
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE cam_app" in ddl
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres" in ddl
    assert "GRANT INSERT" not in ddl
    assert "GRANT UPDATE" not in ddl
    assert "GRANT DELETE" not in ddl
    assert "TO cam_app" not in ddl
    assert random_secret in ddl


def test_catalog_checks_reject_unexpected_output():
    assert operator.role_exists(sql_runner=lambda query: "0") is False
    assert operator.role_exists(sql_runner=lambda query: "1") is True
    with pytest.raises(operator.BackupIdentityGateError):
        operator.role_exists(sql_runner=lambda query: "unknown")
    assert operator.privilege_coverage(
        sql_runner=lambda query: "197|0|1|0|0|0"
    ) == (197, 0, 1, 0, 0, 0)
    with pytest.raises(operator.BackupIdentityGateError):
        operator.privilege_coverage(sql_runner=lambda query: "invalid")


def test_backup_secret_file_is_root_only_and_never_overwritten(tmp_path):
    path = tmp_path / "database-backup.env"
    operator.write_backup_file(path, "runtime-generated-value")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert operator.credentials_file_state(path) == "ROOT_0600"
    with pytest.raises(FileExistsError):
        operator.write_backup_file(path, "should-not-overwrite")
    assert "runtime-generated-value" in path.read_text()
    path.chmod(0o640)
    assert operator.credentials_file_state(path) == "INVALID"
    path.unlink()
    assert operator.credentials_file_state(path) == "MISSING"


def test_status_remains_read_only(tmp_path):
    path = tmp_path / "database-backup.env"
    with patch.object(operator, "endpoint_resolves", return_value=True):
        result = operator.run_status(
            target(), backup_path=path, sql_runner=lambda sql: "0"
        )
    assert result["BACKUP_ROLE"] == "MISSING"
    assert result["APP_ENDPOINT_DNS"] == "OK"
    assert result["FULL_DUMP_STARTED"] == "NO"
    assert result["BACKUP_ENV"] == "MISSING"


def test_provision_creates_scope_with_ephemeral_credential_and_no_dump(tmp_path):
    path = tmp_path / "database-backup.env"
    applied = []
    role_ready = False

    def fake_sql(sql):
        nonlocal role_ready
        if "FROM pg_roles" in sql:
            return "1" if role_ready else "0"
        if "CREATE ROLE cam_backup" in sql:
            assert not role_ready
            role_ready = True
            applied.append(sql)
            return ""
        if "has_table_privilege" in sql:
            return "197|0|1|0|0|0"
        raise AssertionError("Unexpected SQL")

    with patch.object(operator.os, "geteuid", return_value=0), patch.object(
        operator, "endpoint_resolves", return_value=True,
    ), patch.object(operator, "verify_readonly_connection") as verify:
        result = operator.provision(
            target(), sql_runner=fake_sql, backup_path=path
        )
    assert result["BACKUP_ROLE"] == "EXISTS"
    assert result["TABLES_WITHOUT_SELECT"] == 0
    assert result["TABLES_WITH_WRITE"] == 0
    assert result["SEQUENCES_WITH_WRITE"] == 0
    assert result["BACKUP_ENV"] == "ROOT_0600"
    assert len(applied) == 1
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert verify.call_count == 1
    assert verify.call_args.args[0].username == "cam_app"


def test_provision_fails_closed_for_existing_role_or_credentials(tmp_path):
    path = tmp_path / "database-backup.env"
    with patch.object(operator.os, "geteuid", return_value=0):
        with pytest.raises(operator.BackupIdentityGateError):
            operator.provision(
                target(), backup_path=path, sql_runner=lambda query: "1"
            )
        path.write_text("do-not-overwrite")
        with pytest.raises(operator.BackupIdentityGateError):
            operator.provision(
                target(), backup_path=path, sql_runner=lambda query: "0"
            )
    assert path.read_text() == "do-not-overwrite"


def test_provision_rolls_back_file_when_sql_transaction_fails(tmp_path):
    path = tmp_path / "database-backup.env"

    def fake_sql(sql):
        if "CREATE ROLE" in sql:
            raise operator.BackupIdentityGateError("Simulated SQL failure")
        return "0"

    with patch.object(operator.os, "geteuid", return_value=0), patch.object(
        operator, "endpoint_resolves", return_value=True
    ), pytest.raises(operator.BackupIdentityGateError):
        operator.provision(target(), backup_path=path, sql_runner=fake_sql)
    assert not path.exists()


def test_verify_readonly_connection_rejects_unexpected_login():
    class Response:
        returncode = 0
        stdout = "cam_app|creative_asset_manager"

    def runner(*args, **kwargs):
        assert "PGPASSWORD" in kwargs["env"]
        assert kwargs["capture_output"]
        assert all(kwargs["env"]["PGPASSWORD"] not in str(part) for part in args[0])
        return Response()

    with pytest.raises(operator.BackupIdentityGateError):
        operator.verify_readonly_connection(
            target(), operator.secrets.token_urlsafe(32), runner=runner
        )
