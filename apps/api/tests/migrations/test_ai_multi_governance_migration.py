import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class AiMultiGovernanceMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0021_ai_multi_governance")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn("ai_runtime_controls", inspector.get_table_names())
            self.assertIn("ai_budget_overrides", inspector.get_table_names())
            provider_columns = {c["name"] for c in inspector.get_columns("tenant_provider_policies")}
            self.assertTrue({
                "single_enabled", "batch_enabled", "emergency_stop",
                "single_active_jobs_limit", "batch_active_jobs_limit",
                "daily_budget_limit_micros", "monthly_budget_limit_micros",
                "allowed_models_json",
            } <= provider_columns)
            rate_columns = {c["name"] for c in inspector.get_columns("ai_cost_rates")}
            self.assertIn("processing_mode", rate_columns)
            engine.dispose()

            command.downgrade(config, "0020_ai_analysis_requests")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertNotIn("ai_runtime_controls", inspector.get_table_names())
            self.assertNotIn("ai_budget_overrides", inspector.get_table_names())
            self.assertNotIn(
                "processing_mode",
                {c["name"] for c in inspector.get_columns("ai_cost_rates")},
            )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
