import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


if __name__ == "__main__":
    unittest.main()
