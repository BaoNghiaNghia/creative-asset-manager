"""Disposable DDL coverage for 0167 (no production schema mutation)."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

MIGRATION = (Path(__file__).resolve().parents[5] /
             "database/migrations/versions/0167_bdna_scan_result_receipts.py")


def module():
    spec = importlib.util.spec_from_file_location("bdna_0167", MIGRATION)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_quarantine_result_ddl_has_composite_fks_and_guarded_downgrade(monkeypatch):
    revision = module()
    assert revision.revision == "0167_bdna_scan_result_receipts"
    assert revision.down_revision == "0166_bdna_scan_authorization_reviews"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql(
                "CREATE TABLE bc_scan_tasks "
                "(id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(255) NOT NULL, "
                "UNIQUE (tenant_id, id))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE bc_listing_snapshots "
                "(id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(255) NOT NULL, "
                "shop_id VARCHAR(36) NOT NULL, UNIQUE (tenant_id, shop_id, id))"
            )
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
                table = "bc_scan_result_receipts"
                assert table in inspect(connection).get_table_names()
                names = {c["name"] for c in inspect(connection).get_columns(table)}
                assert {"tenant_id", "shop_id", "task_id", "snapshot_id",
                        "snapshot_sha256", "lease_generation", "client_receipt_id",
                        "client_payload_sha256", "manifest_sha256",
                        "manifest_json", "status", "received_at"} <= names
                refs = inspect(connection).get_foreign_keys(table)
                assert any(f["referred_table"] == "bc_scan_tasks"
                           and f["constrained_columns"] == ["tenant_id", "task_id"]
                           for f in refs)
                assert any(f["referred_table"] == "bc_listing_snapshots"
                           and f["constrained_columns"] ==
                               ["tenant_id", "shop_id", "snapshot_id"]
                           for f in refs)
                connection.exec_driver_sql(
                    "INSERT INTO bc_scan_tasks (id,tenant_id) VALUES (?,?)",
                    ("task-1","tenant-a"),
                )
                connection.exec_driver_sql(
                    "INSERT INTO bc_listing_snapshots "
                    "(id,tenant_id,shop_id) VALUES (?,?,?)",
                    ("snap-1","tenant-a","shop-1"),
                )
                base = (
                    "receipt-1","tenant-a","task-1","shop-1","snap-1","a"*64,
                    "agent-1","lease-1",1,"client-1","b"*64,"c"*64,
                    '{"listing_key":"example"}',1,1,"quarantined_unverified",
                    "2026-10-18T00:00:00Z",
                )
                statement = (
                    "INSERT INTO bc_scan_result_receipts "
                    "(id,tenant_id,task_id,shop_id,snapshot_id,snapshot_sha256,"
                    "agent_id,lease_id,lease_generation,client_receipt_id,"
                    "client_payload_sha256,manifest_sha256,manifest_json,"
                    "gallery_count,page_count,status,received_at) "
                    "VALUES (" + ",".join("?" for _ in range(len(base))) + ")"
                )
                connection.exec_driver_sql(statement,base)
                for updates in [
                    {1:"tenant-b",0:"receipt-2",9:"client-2"},
                    {3:"shop-wrong",0:"receipt-3",9:"client-3"},
                    {0:"receipt-4",9:"client-4"},
                    {0:"receipt-5",8:2,9:"client-1"},
                    {0:"receipt-6",8:2,9:"client-6",15:"verified"},
                    {0:"receipt-7",8:2,9:"client-7",13:21},
                ]:
                    changed=list(base)
                    for key,value in updates.items():
                        changed[key]=value
                    with pytest.raises(IntegrityError):
                        with connection.begin_nested():
                            connection.exec_driver_sql(statement,tuple(changed))
                monkeypatch.delenv("CAM_ALLOW_SCAN_RESULT_DATA_DROP_FOR_TEST",raising=False)
                with pytest.raises(RuntimeError,match="protected"):
                    revision.downgrade()
                assert table in inspect(connection).get_table_names()
    finally:
        engine.dispose()
