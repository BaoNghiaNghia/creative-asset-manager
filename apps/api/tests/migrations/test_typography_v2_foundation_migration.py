"""Typography V2 additive migration and guarded rollback on SQLite.

Alembic is executed in a child process so importing its global migration
environment never contaminates Base.metadata for unrelated unit tests.
PostgreSQL migration validation is performed by CI's migration jobs.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.model import RrugcReferenceAssetModel
from app.modules.realistic_review_ugc.typography_model import RrugcTypographyReferenceModel


def migration(db_path: Path, op: str, target: str, *, expected_failure: str | None = None):
    api_root = Path(__file__).resolve().parents[2]
    script = (
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "import sys\n"
        "cfg=Config(sys.argv[1])\n"
        "cfg.set_main_option('sqlalchemy.url', 'sqlite:///' + sys.argv[2])\n"
        "getattr(command, sys.argv[3])(cfg, sys.argv[4])\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(api_root / "alembic.ini"), str(db_path), op, target],
        cwd=api_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=75,
    )
    if expected_failure is None:
        assert completed.returncode == 0, completed.stderr[-5000:]
    else:
        assert completed.returncode != 0
        assert expected_failure in completed.stderr, completed.stderr[-5000:]


def test_typography_foundation_migrates_and_refuses_destructive_rollback():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "schema.db"
        migration(path, "upgrade", "head")
        db = create_engine(f"sqlite:///{path}")
        inspector = inspect(db)
        assert "rrugc_reference_assets" in inspector.get_table_names()
        assert "rrugc_typography_references" in inspector.get_table_names()
        assert "rrugc_typography_style_dna" in inspector.get_table_names()
        reference_fks = inspector.get_foreign_keys("rrugc_typography_references")
        assert any(
            fk["referred_table"] == "rrugc_reference_assets"
            and fk["constrained_columns"] == ["tenant_id", "asset_id"]
            for fk in reference_fks
        )
        assert any(
            item["name"] == "uq_typography_reference_tenant_hash"
            for item in inspector.get_unique_constraints("rrugc_typography_references")
        )
        # Downgrade must never erase reference metadata with a live link.
        with Session(db) as session:
            parent = RrugcReferenceAssetModel(
                id="asset-1", tenant_id="tenant-a", source_type="upload",
                source_key="a" * 64, content_hash="a" * 64,
                remote_file_id="drive-1", created_by_user_id="owner",
                reference_type="other", status="ready",
            )
            session.add(parent)
            session.flush()
            session.add(RrugcTypographyReferenceModel(
                id="typo-1", tenant_id="tenant-a", workspace_id="tenant-a",
                asset_id=parent.id, content_hash=parent.content_hash,
                created_by_user_id="owner",
            ))
            session.commit()
        db.dispose()

        migration(path, "downgrade", "0187_navbar_member_permissions",
                  expected_failure="typography_v2_data_must_be_archived_before_downgrade")
        db = create_engine(f"sqlite:///{path}")
        assert "rrugc_typography_references" in inspect(db).get_table_names()
        with Session(db) as session:
            row = session.get(RrugcTypographyReferenceModel, "typo-1")
            assert row is not None
            session.delete(row)
            session.commit()
        db.dispose()
        migration(path, "downgrade", "0187_navbar_member_permissions")
        db = create_engine(f"sqlite:///{path}")
        tables = inspect(db).get_table_names()
        assert "rrugc_typography_references" not in tables
        assert "rrugc_typography_style_dna" not in tables
        assert "rrugc_reference_assets" in tables
        db.dispose()
