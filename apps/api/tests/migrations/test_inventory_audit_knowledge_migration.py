from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


API_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = API_ROOT / "alembic.ini"


def test_0087_adds_inventory_audit_knowledge_and_carry_forward_provenance(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'audit-knowledge.sqlite'}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0086_source_asset_parent_listing")
    command.upgrade(config, "0087_inventory_audit_knowledge")

    engine = create_engine(url)
    inspector = inspect(engine)
    assert {"inventory_operation_audits", "inventory_operation_changes", "inventory_knowledge_entries"} <= set(inspector.get_table_names())
    carry_columns = {column["name"] for column in inspector.get_columns("inventory_daily_carry_forwards")}
    assert {"knowledge_hash", "knowledge_version"} <= carry_columns
    audit_indexes = {index["name"] for index in inspector.get_indexes("inventory_operation_audits")}
    job_indexes = {index["name"] for index in inspector.get_indexes("inventory_jobs")}
    knowledge_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("inventory_knowledge_entries")
    }
    knowledge_uniques = {
        item["name"]
        for item in inspector.get_unique_constraints("inventory_knowledge_entries")
    }
    assert "ix_inventory_operation_audits_tenant_date" in audit_indexes
    assert "ix_inventory_jobs_tenant_type_entity" in job_indexes
    assert "ix_inventory_knowledge_tenant_status" in knowledge_indexes
    assert bool(knowledge_indexes["uq_inventory_knowledge_active_key"]["unique"]) is True
    assert "uq_inventory_knowledge_proposal_source" in knowledge_uniques
    engine.dispose()

    command.downgrade(config, "0086_source_asset_parent_listing")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "inventory_operation_audits" not in inspector.get_table_names()
    carry_columns = {column["name"] for column in inspector.get_columns("inventory_daily_carry_forwards")}
    assert "knowledge_hash" not in carry_columns
    assert "knowledge_version" not in carry_columns
    engine.dispose()
