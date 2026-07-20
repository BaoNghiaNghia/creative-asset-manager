import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

class AiGovernanceMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"migration.db"
            config=Config("alembic.ini")
            config.set_main_option("sqlalchemy.url",f"sqlite:///{path}")
            command.upgrade(config,"head")
            engine=create_engine(f"sqlite:///{path}")
            inspector=inspect(engine)
            required={"ai_cost_rates","tenant_ai_budget_policies","ai_budget_accounts",
                "ai_budget_reservations","ai_usage_records","ai_budget_events",
                "ai_pilot_runs","ai_pilot_items"}
            self.assertTrue(required <= set(inspector.get_table_names()))
            self.assertIn("currency",{c["name"] for c in inspector.get_columns("tenant_ai_budget_policies")})
            engine.dispose()
            command.downgrade(config,"0010_tenant_processing_policies")
            engine=create_engine(f"sqlite:///{path}")
            inspector=inspect(engine)
            self.assertFalse(required & set(inspector.get_table_names()))
            self.assertIn("asset_ai_analyses",inspector.get_table_names())
            engine.dispose()
