import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

PATH = Path(__file__).resolve().parents[4] / "database/migrations/versions/0024_ai_operations_configuration.py"


class AiOperationsConfigurationMigrationTest(unittest.TestCase):
    def test_upgrade_and_downgrade(self):
        spec = importlib.util.spec_from_file_location("migration_0024", PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        self.assertEqual(module.down_revision, "0023_ai_operations_controls")
        fake = MagicMock()
        batch = fake.batch_alter_table.return_value.__enter__.return_value
        with patch.object(module, "op", fake):
            module.upgrade()
            fake.batch_alter_table.assert_called_once_with("tenant_processing_policies")
            self.assertEqual(batch.add_column.call_count, 6)
            self.assertEqual(batch.create_check_constraint.call_count, 2)
            fake.reset_mock()
            batch.reset_mock()
            module.downgrade()
            fake.batch_alter_table.assert_called_once_with("tenant_processing_policies")
            self.assertEqual(batch.drop_constraint.call_count, 2)
            self.assertEqual(batch.drop_column.call_count, 6)


    def test_sqlite_round_trip_preserves_existing_policy_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "migration.db"
            url = f"sqlite:///{path}"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "0023_ai_operations_controls")
            engine = create_engine(url)
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO tenant_processing_policies (tenant_id, updated_at) "
                    "VALUES ('tenant-before-0024', CURRENT_TIMESTAMP)"
                ))
            engine.dispose()

            command.upgrade(config, "0024_ai_operations_configuration")
            engine = create_engine(url)
            inspector = inspect(engine)
            constraints = {
                item["name"]
                for item in inspector.get_check_constraints("tenant_processing_policies")
            }
            with engine.connect() as connection:
                row = connection.execute(text(
                    "SELECT default_ai_mode, auto_analyze_new_assets, "
                    "daily_ai_item_limit, ai_retry_count, ai_timeout_seconds "
                    "FROM tenant_processing_policies "
                    "WHERE tenant_id = 'tenant-before-0024'"
                )).one()
            self.assertEqual(tuple(row), ("single", 0, 100, 2, 60))
            self.assertIn("ck_tenant_policy_default_ai_mode", constraints)
            self.assertIn("ck_tenant_policy_ai_ops_limits", constraints)
            engine.dispose()

            command.downgrade(config, "0023_ai_operations_controls")
            engine = create_engine(url)
            columns = {
                item["name"]
                for item in inspect(engine).get_columns("tenant_processing_policies")
            }
            self.assertNotIn("default_ai_mode", columns)
            with engine.connect() as connection:
                count = connection.execute(text(
                    "SELECT COUNT(*) FROM tenant_processing_policies "
                    "WHERE tenant_id = 'tenant-before-0024'"
                )).scalar_one()
            self.assertEqual(count, 1)
            engine.dispose()

if __name__ == "__main__":
    unittest.main()
