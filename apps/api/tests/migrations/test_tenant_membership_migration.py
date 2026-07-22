import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class TenantMembershipMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenant-membership.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0026_tenant_memberships")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertIn("tenants", inspector.get_table_names())
            self.assertIn("tenant_memberships", inspector.get_table_names())
            membership_indexes = {item["name"] for item in inspector.get_indexes("tenant_memberships")}
            self.assertIn("ix_tenant_memberships_user_status", membership_indexes)
            self.assertIn("ix_tenant_memberships_tenant_status", membership_indexes)
            session_columns = {item["name"] for item in inspector.get_columns("auth_sessions")}
            self.assertIn("active_tenant_id", session_columns)
            engine.dispose()

            command.downgrade(config, "0025_application_users")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            self.assertNotIn("tenant_memberships", inspector.get_table_names())
            self.assertNotIn("tenants", inspector.get_table_names())
            self.assertIn("users", inspector.get_table_names())
            session_columns = {item["name"] for item in inspector.get_columns("auth_sessions")}
            self.assertNotIn("active_tenant_id", session_columns)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
