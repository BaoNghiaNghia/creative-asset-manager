import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class CentralAuthorizationMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central-authorization.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0028_central_authorization")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn(
                "platform_admin_assignments", inspector.get_table_names()
            )
            unique_columns = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(
                    "platform_admin_assignments"
                )
            }
            self.assertIn(("user_id",), unique_columns)
            self.assertEqual(
                len(inspector.get_foreign_keys("platform_admin_assignments")), 2
            )
            engine.dispose()

            command.downgrade(config, "0027_tenant_rbac")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertNotIn(
                "platform_admin_assignments", inspector.get_table_names()
            )
            self.assertIn("roles", inspector.get_table_names())
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
