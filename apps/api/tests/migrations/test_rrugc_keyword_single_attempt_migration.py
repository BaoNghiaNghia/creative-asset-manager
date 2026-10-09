"""Verify the Stage 1 single-attempt migration on queued and retrying work."""
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, text


def test_single_attempt_migration_preserves_unrelated_jobs(monkeypatch):
    migration = Path(__file__).resolve().parents[4] / "database/migrations/versions/0146_rrugc_keyword_single_attempt.py"
    spec = importlib.util.spec_from_file_location("rrugc_keyword_single_attempt_test", migration)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE processing_jobs (
                id TEXT PRIMARY KEY, job_type TEXT, status TEXT,
                attempt_count INTEGER, max_attempts INTEGER,
                last_error_code TEXT, last_error_message TEXT, completed_at TEXT)
        """)
        connection.exec_driver_sql("""
            CREATE TABLE rrugc_keyword_image_jobs (
                id TEXT PRIMARY KEY, processing_job_id TEXT,
                status TEXT, last_error_code TEXT, last_error_message TEXT)
        """)
        connection.execute(text("""
            INSERT INTO processing_jobs
              (id, job_type, status, attempt_count, max_attempts)
            VALUES
              ('pending-first', 'rrugc_keyword_image_generate', 'pending', 0, 3),
              ('retry-old', 'rrugc_keyword_image_generate', 'retry', 2, 3),
              ('working', 'rrugc_keyword_image_generate', 'processing', 1, 3),
              ('unrelated', 'rrugc_colorway_generate', 'retry', 2, 3)
        """))
        connection.execute(text("""
            INSERT INTO rrugc_keyword_image_jobs (id, processing_job_id, status)
            VALUES
              ('first', 'pending-first', 'queued'),
              ('old', 'retry-old', 'queued'),
              ('active', 'working', 'running')
        """))
        monkeypatch.setattr(module.op, "execute", connection.exec_driver_sql)
        module.upgrade()
        jobs = connection.execute(text(
            "SELECT id, status, max_attempts FROM processing_jobs ORDER BY id"
        )).all()
        assert jobs == [
            ("pending-first", "pending", 1),
            ("retry-old", "failed", 1),
            ("unrelated", "retry", 3),
            ("working", "processing", 1),
        ]
        image_jobs = connection.execute(text(
            "SELECT id, status FROM rrugc_keyword_image_jobs ORDER BY id"
        )).all()
        assert image_jobs == [
            ("active", "running"), ("first", "queued"), ("old", "failed"),
        ]
    engine.dispose()
