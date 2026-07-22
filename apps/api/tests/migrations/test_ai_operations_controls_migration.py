import importlib.util
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch


PATH = (
    Path(__file__).resolve().parents[4]
    / "database/migrations/versions/0023_ai_operations_controls.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("migration_0023", PATH)
    value = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(value)
    return value


class AiOperationsControlsMigrationTest(unittest.TestCase):
    def test_revision_chain_and_upgrade_downgrade(self):
        migration = _module()
        self.assertEqual(migration.revision, "0023_ai_operations_controls")
        self.assertEqual(migration.down_revision, "0022_ai_operations_indexes")
        fake = MagicMock()
        with patch.object(migration, "op", fake):
            migration.upgrade()
            self.assertEqual(fake.add_column.call_count, 6)
            fake.create_index.assert_called_once()
            fake.reset_mock()
            migration.downgrade()
            fake.drop_index.assert_called_once_with(
                "ix_processing_jobs_cancel_eligibility", table_name="processing_jobs"
            )
            self.assertEqual(fake.drop_column.call_count, 6)
            self.assertEqual(
                fake.drop_column.call_args_list[-2:],
                [
                    call("tenant_processing_policies", "default_ai_model"),
                    call("tenant_processing_policies", "default_ai_provider"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
