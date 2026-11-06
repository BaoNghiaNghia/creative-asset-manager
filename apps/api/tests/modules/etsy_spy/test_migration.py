"""Alembic migration 0195 smoke on an isolated disposable database.

Never points at the production CAM database.
"""
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, MetaData, Table, Column, String, DateTime, select, text, inspect

from app.modules.etsy_spy.model import (
    EtsySpyAgent, EtsySpySession, EtsySpyKeyword, EtsySpyListing,
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch, EtsySpyBlacklist,
)


def test_migration_adds_tables_and_admin_only_permissions(tmp_path):
    db = tmp_path / "etsy-spy-migration-test.sqlite"
    url = "sqlite:///" + str(db)
    engine = create_engine(url)
    metadata = MetaData()
    Table("permissions", metadata,
          Column("id", String(36), primary_key=True),
          Column("permission_key", String(128)),
          Column("description", String()),
          Column("status", String()),
          Column("created_at", DateTime()),
          Column("updated_at", DateTime()))
    Table("roles", metadata,
          Column("id", String(36), primary_key=True),
          Column("tenant_id", String(255)),
          Column("role_key", String(128)),
          Column("status", String()))
    Table("role_permissions", metadata,
          Column("id", String(36), primary_key=True),
          Column("role_id", String(36)),
          Column("permission_id", String(36)),
          Column("created_at", DateTime()))
    metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as c:
        for role_id, role_key in (("admin-1", "tenant_admin"), ("viewer-1", "viewer")):
            c.execute(text(
                "INSERT INTO roles (id,tenant_id,role_key,status) "
                "VALUES (:id,'boostgame',:key,'active')"
            ), {"id": role_id, "key": role_key})

    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    command.stamp(config, "0194_typography_g3_pilot_shortlist")
    command.upgrade(config, "head")

    expected = {
        EtsySpyAgent.__tablename__, EtsySpySession.__tablename__,
        EtsySpyKeyword.__tablename__, EtsySpyListing.__tablename__,
        EtsySpyTrademark.__tablename__, EtsySpyLog.__tablename__,
        EtsySpyBatch.__tablename__, EtsySpyBlacklist.__tablename__,
    }
    with engine.connect() as c:
        tables = set(inspect(c).get_table_names())
        assert expected.issubset(tables)
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0197_etsy_spy_blacklist"
        assert {"is_current", "has_search", "has_shop"}.issubset({
            column["name"] for column in inspect(c).get_columns("etsy_spy_listings")})
        assert {"last_inventory_at", "last_inventory_count"}.issubset({
            column["name"] for column in inspect(c).get_columns("etsy_spy_agents")})
        permissions = c.execute(text(
            "SELECT permission_key FROM permissions WHERE permission_key LIKE 'etsy_spy.%'"
        )).scalars().all()
        assert set(permissions) == {"etsy_spy.read", "etsy_spy.manage"}
        assigned = c.execute(text(
            "SELECT r.role_key,p.permission_key FROM role_permissions rp "
            "JOIN roles r ON r.id=rp.role_id "
            "JOIN permissions p ON p.id=rp.permission_id "
            "WHERE p.permission_key LIKE 'etsy_spy.%'"
        )).all()
        assert set(assigned) == {("tenant_admin", "etsy_spy.read"),
                                 ("tenant_admin", "etsy_spy.manage")}
    # Blacklist downgrade is reversible and preserves all original Etsy tables.
    command.downgrade(config, "0196_etsy_spy_inventory")
    with engine.connect() as c:
        assert "etsy_spy_blacklist" not in inspect(c).get_table_names()
    command.downgrade(config, "0195_etsy_spy_projection")
    with engine.connect() as c:
        assert expected - {"etsy_spy_blacklist"} <= set(inspect(c).get_table_names())
        assert "is_current" not in {
            column["name"] for column in inspect(c).get_columns("etsy_spy_listings")}
    # Empty 0195 tables can still be downgraded without deleting source history.
    command.downgrade(config, "0194_typography_g3_pilot_shortlist")
    with engine.connect() as c:
        assert not expected.intersection(inspect(c).get_table_names())
    engine.dispose()
