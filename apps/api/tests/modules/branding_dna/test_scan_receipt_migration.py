"""Disposable DDL contract for 0165: durable receipts with guarded rollback."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError


MIGRATION = Path(__file__).resolve().parents[5] / "database/migrations/versions/0165_bdna_scan_failure_receipts.py"


def migration():
    spec = importlib.util.spec_from_file_location("bdna_0165", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scan_receipt_migration_additive_and_downgrade_fails_closed(monkeypatch):
    rev = migration()
    assert rev.revision == "0165_bdna_scan_failure_receipts"
    assert rev.down_revision == "0164_branding_dna_shop_rag"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE bc_scan_tasks (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(255) NOT NULL)")
            with Operations.context(MigrationContext.configure(conn)):
                rev.upgrade()
                assert "bc_scan_failure_receipts" in inspect(conn).get_table_names()
                assert any(row["name"] == "ux_bc_scan_task_tenant_identity" and row["unique"]
                           for row in inspect(conn).get_indexes("bc_scan_tasks"))
                conn.exec_driver_sql(
                    "INSERT INTO bc_scan_tasks(id, tenant_id) VALUES (?, ?)",
                    ("12345678-2222-4222-8222-123456789012", "tenant-a"),
                )
                with pytest.raises(IntegrityError):
                    # A forged tenant cannot attach a receipt to another tenant's job.
                    with conn.begin_nested():
                        conn.exec_driver_sql(
                            "INSERT INTO bc_scan_failure_receipts "
                            "(tenant_id, task_id, lease_generation, lease_id, agent_id, "
                            "error_code, status, received_at) VALUES (?,?,?,?,?,?,?,?)",
                            ("tenant-b", "12345678-2222-4222-8222-123456789012", 1,
                             "12345678-3333-4333-8333-123456789012",
                             "12345678-4444-4444-8444-123456789012",
                             "rate_limited", "retry", "2026-10-18T00:00:00Z"),
                        )
                cols = {r["name"] for r in inspect(conn).get_columns("bc_scan_failure_receipts")}
                assert {"tenant_id", "task_id", "lease_generation", "lease_id",
                        "agent_id", "error_code", "status", "retry_after"} <= cols
                monkeypatch.delenv("CAM_ALLOW_SCAN_RECEIPT_DATA_DROP_FOR_TEST", raising=False)
                with pytest.raises(RuntimeError, match="protected"):
                    rev.downgrade()
                assert "bc_scan_failure_receipts" in inspect(conn).get_table_names()
    finally:
        engine.dispose()
