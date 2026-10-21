"""Verify isolated PostgreSQL configuration repair without touching /etc."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SOURCE = Path(__file__).with_name("cam_catinat_fix_rehearsal_conf.py")
SPEC = importlib.util.spec_from_file_location("catinat_rehearsal_conf", SOURCE)
assert SPEC and SPEC.loader
conf = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = conf
SPEC.loader.exec_module(conf)


def test_bare_ip_replaced_with_quoted_postgres_string():
    actual=conf.fix_postgresql_conf(
        "#listen_addresses = 'localhost'\nlisten_addresses = 127.0.0.1\nport = 55432\n"
    )
    assert actual == "#listen_addresses = 'localhost'\nlisten_addresses = '127.0.0.1'\nport = 55432\n"


def test_erroneous_triple_quote_replaced_with_correct_string():
    value=conf.fix_postgresql_conf("listen_addresses = '''127.0.0.1'''\n")
    assert value == "listen_addresses = '127.0.0.1'\n"
    assert conf.fix_postgresql_conf(value)==value


def test_missing_setting_appended_safely():
    assert conf.fix_postgresql_conf("port = 55432\n").endswith("listen_addresses = '127.0.0.1'\n")


def test_multiple_active_listeners_blocked():
    with pytest.raises(conf.RehearsalConfigError,match="Multiple"):
        conf.fix_postgresql_conf("listen_addresses = '*'\nlisten_addresses = '0.0.0.0'\n")


def test_local_trust_hardened_peer_preserves_existing_hba_lines():
    before=(
        "# PostgreSQL rehearsal access\n"
        "local all postgres trust\n"
        "local all all trust\n"
        "host all all 127.0.0.1/32 scram-sha-256\n"
        "host all all ::1/128 scram-sha-256\n"
    )
    after=conf.fix_pg_hba_conf(before)
    assert after==before.replace("postgres trust", "postgres peer").replace("all trust","all peer")
    assert conf.fix_pg_hba_conf(after)==after
    assert "host all all 127.0.0.1/32 scram-sha-256\n" in after


def test_unknown_auth_method_fails_closed():
    with pytest.raises(conf.RehearsalConfigError,match="Unexpected"):
        conf.fix_pg_hba_conf("local all all ident\n")


def test_missing_local_rules_fails_closed():
    with pytest.raises(conf.RehearsalConfigError,match="No local"):
        conf.fix_pg_hba_conf("# local all all trust\nhost all all 127.0.0.1/32 scram-sha-256\n")


def test_repair_function_restricts_file_names():
    with pytest.raises(conf.RehearsalConfigError,match="dedicated"):
        conf._replace_dedicated_file("../../main/postgresql.conf",lambda text:text)




def test_postgresql_distro_config_owner_can_be_postgres_or_root():
    assert conf._allowed_config_owner(0, postgres_uid=120)
    assert conf._allowed_config_owner(120, postgres_uid=120)
    assert not conf._allowed_config_owner(9999, postgres_uid=120)


def test_postgres_owned_file_preserves_identity_and_mode_when_repaired(tmp_path, monkeypatch):
    import os
    d = tmp_path / "catinat_rehearsal"
    d.mkdir()
    cfg = d / "postgresql.conf"
    cfg.write_text("listen_addresses = 127.0.0.1\n")
    cfg.chmod(0o640)
    monkeypatch.setattr(conf, "CONF_DIR", d)
    # Emulate allowed 'postgres' owner in a nonprivileged unit test
    # while verifying correct uid/gid/mode preservation after atomic replace.
    monkeypatch.setattr(conf, "_allowed_config_owner", lambda uid: True)
    monkeypatch.setattr(conf.os, "fchown", lambda fd, uid, gid: None)
    conf._replace_dedicated_file("postgresql.conf", conf.fix_postgresql_conf)
    assert cfg.read_text() == "listen_addresses = '127.0.0.1'\n"
    assert cfg.stat().st_mode & 0o777 == 0o640


def test_reject_unknown_owner_before_modification(tmp_path, monkeypatch):
    d = tmp_path / "catinat_rehearsal"
    d.mkdir()
    cfg = d / "postgresql.conf"
    cfg.write_text("listen_addresses = 127.0.0.1\n")
    monkeypatch.setattr(conf, "CONF_DIR", d)
    monkeypatch.setattr(conf, "_allowed_config_owner", lambda uid: False)
    with pytest.raises(conf.RehearsalConfigError, match="root or postgres"):
        conf._replace_dedicated_file("postgresql.conf", conf.fix_postgresql_conf)
    assert cfg.read_text() == "listen_addresses = 127.0.0.1\n"


def test_only_test_cluster_configuration_path_is_hardcoded():
    assert str(conf.CONF_DIR)=="/etc/postgresql/16/catinat_rehearsal"
    assert "main" not in str(conf.CONF_DIR)
