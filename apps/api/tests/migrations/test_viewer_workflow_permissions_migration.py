import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa


EXPECTED_PERMISSIONS = {
    "public_review.read",
    "public_review.resolve",
    "realistic_review_ugc.read",
    "realistic_review_ugc.run",
    "realistic_review_ugc.configure",
}


def load_migration():
    root = Path(__file__).resolve().parents[4]
    path = root / "database" / "migrations" / "versions" / "0122_viewer_workflow_permissions.py"
    spec = importlib.util.spec_from_file_location("viewer_workflow_permissions_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ViewerWorkflowPermissionsMigrationTest(unittest.TestCase):
    def test_existing_viewer_receives_review_workflow_permissions(self):
        migration = load_migration()
        engine = sa.create_engine("sqlite:///:memory:")
        metadata = sa.MetaData()
        permissions = sa.Table(
            "permissions",
            metadata,
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("permission_key", sa.String, nullable=False, unique=True),
            sa.Column("description", sa.Text),
            sa.Column("status", sa.String),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )
        roles = sa.Table(
            "roles",
            metadata,
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("role_key", sa.String, nullable=False),
            sa.Column("description", sa.Text),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )
        role_permissions = sa.Table(
            "role_permissions",
            metadata,
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("role_id", sa.String, nullable=False),
            sa.Column("permission_id", sa.String, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("role_id", "permission_id"),
        )
        metadata.create_all(engine)

        with engine.begin() as connection:
            connection.execute(
                roles.insert().values(
                    id="viewer-role",
                    role_key="viewer",
                    description="Old viewer",
                )
            )
            with patch.object(migration.op, "get_bind", return_value=connection):
                migration.upgrade()

            granted = set(
                connection.execute(
                    sa.select(permissions.c.permission_key)
                    .select_from(
                        role_permissions.join(
                            permissions,
                            permissions.c.id == role_permissions.c.permission_id,
                        )
                    )
                    .where(role_permissions.c.role_id == "viewer-role")
                ).scalars()
            )
            description = connection.scalar(
                sa.select(roles.c.description).where(roles.c.id == "viewer-role")
            )

        self.assertEqual(granted, EXPECTED_PERMISSIONS)
        self.assertIn("Realistic Review UGC", description)
        self.assertIn("Review Board", description)


if __name__ == "__main__":
    unittest.main()
