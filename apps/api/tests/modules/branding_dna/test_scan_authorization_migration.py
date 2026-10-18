"""Disposable authorization review DDL contract and protective downgrade."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

MIGRATION = (Path(__file__).resolve().parents[5] /
             "database/migrations/versions/0166_bdna_scan_authorization_reviews.py")


def test_authorization_review_migration_is_additive_and_tenant_fenced(monkeypatch):
    spec = importlib.util.spec_from_file_location("bdna_0166", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0166_bdna_scan_authorization_reviews"
    assert module.down_revision == "0165_bdna_scan_failure_receipts"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql(
                "CREATE TABLE bc_knowledge_documents ("
                "tenant_id VARCHAR(255) NOT NULL, shop_id VARCHAR(36) NOT NULL, "
                "id VARCHAR(36) PRIMARY KEY, "
                "UNIQUE(tenant_id,shop_id,id))"
            )
            connection.exec_driver_sql(
                "INSERT INTO bc_knowledge_documents(tenant_id,shop_id,id) VALUES (?,?,?)",
                ("tenant-a", "12345678-1111-4111-8111-123456789012",
                 "12345678-2222-4222-8222-123456789012")
            )
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                columns = {c["name"] for c in inspect(connection).get_columns(
                    "bc_scan_authorization_reviews")}
                assert {"tenant_id", "shop_id", "document_id", "document_sha256",
                        "storefront_sha256", "expires_at", "status", "revoked_by",
                        "revoked_at", "revocation_note"} <= columns
                with pytest.raises(IntegrityError):
                    with connection.begin_nested():
                        connection.exec_driver_sql(
                            "INSERT INTO bc_scan_authorization_reviews "
                            "(id,tenant_id,shop_id,document_id,document_sha256,"
                            "platform,storefront_sha256,policy_scope,status,submitted_by,"
                            "review_note,revocation_note,expires_at,created_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            ("12345678-3333-4333-8333-123456789012",
                             "tenant-b", "12345678-1111-4111-8111-123456789012",
                             "12345678-2222-4222-8222-123456789012",
                             "a"*64, "etsy", "b"*64, "etsy_written_access",
                             "pending_review", "tester", "", "",
                             "2026-11-01T00:00:00Z", "2026-10-18T00:00:00Z")
                        )
                monkeypatch.delenv("CAM_ALLOW_SCAN_REVIEW_DATA_DROP_FOR_TEST", raising=False)
                with pytest.raises(RuntimeError, match="protected"):
                    module.downgrade()
                assert "bc_scan_authorization_reviews" in inspect(connection).get_table_names()
    finally:
        engine.dispose()
