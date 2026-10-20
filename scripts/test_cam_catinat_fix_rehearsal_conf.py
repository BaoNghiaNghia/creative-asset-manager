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


def test_only_test_cluster_configuration_path_is_hardcoded():
    assert str(conf.CONF_DIR)=="/etc/postgresql/16/catinat_rehearsal"
    assert "main" not in str(conf.CONF_DIR)
