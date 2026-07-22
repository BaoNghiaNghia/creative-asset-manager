import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class TenantRbacMigrationTest(unittest.TestCase):
    def test_upgrade_and_step_scoped_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tenant-rbac.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0027_tenant_rbac")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertTrue({"permissions", "roles", "role_permissions", "membership_roles"} <= tables)
            role_uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("roles")}
            self.assertIn(("tenant_id", "role_key"), role_uniques)
            membership_role_uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints("membership_roles")}
            self.assertIn(("tenant_membership_id", "role_id"), membership_role_uniques)
            self.assertEqual(len(inspector.get_foreign_keys("membership_roles")), 2)
            engine.dispose()

            command.downgrade(config, "0026_tenant_memberships")
            engine = create_engine(f"sqlite:///{path}")
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            self.assertNotIn("membership_roles", tables)
            self.assertNotIn("roles", tables)
            self.assertIn("tenant_memberships", tables)
            membership_uniques = {item["name"] for item in inspector.get_unique_constraints("tenant_memberships")}
            self.assertNotIn("uq_tenant_memberships_tenant_id_id", membership_uniques)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
