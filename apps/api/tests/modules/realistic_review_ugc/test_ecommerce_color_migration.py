"""Verify the Ecommerce color migration never drops historical outputs."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.runtime.migration import MigrationContext
from alembic.operations import Operations


def test_existing_ecommerce_rows_survive_color_migration():
    root = Path(__file__).resolve().parents[5]
    migration_path = root / "database/migrations/versions/0182_ecommerce_color_scoped_jobs.py"
    spec = spec_from_file_location("ecommerce_color_migration", migration_path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE rrugc_ecommerce_jobs (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(255) NOT NULL,
                source_plan_id VARCHAR(36) NOT NULL,
                source_revision VARCHAR(64) NOT NULL,
                stage VARCHAR(16) NOT NULL,
                output_remote_file_id VARCHAR(255),
                CONSTRAINT uq_rrugc_ecommerce_source_revision_stage
                UNIQUE (tenant_id, source_plan_id, source_revision, stage)
            )
        """))
        conn.execute(sa.text("""
            INSERT INTO rrugc_ecommerce_jobs
            (id,tenant_id,source_plan_id,source_revision,stage,output_remote_file_id)
            VALUES ('legacy-1','t1','p1','r1','prod_s2_ec1','existing-output')
        """))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        legacy = conn.execute(sa.text(
            "SELECT color_key,output_remote_file_id FROM rrugc_ecommerce_jobs WHERE id='legacy-1'"
        )).one()
        assert legacy == ("natural-black", "existing-output")
        conn.execute(sa.text("""
            INSERT INTO rrugc_ecommerce_jobs
            (id,tenant_id,source_plan_id,source_revision,stage,color_key)
            VALUES ('new-1','t1','p1','r1','prod_s2_ec1','natural-red')
        """))
        assert conn.scalar(sa.text("SELECT COUNT(*) FROM rrugc_ecommerce_jobs")) == 2
    engine.dispose()
