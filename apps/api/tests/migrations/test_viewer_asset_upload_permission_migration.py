import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


class ViewerAssetUploadPermissionMigrationTest(unittest.TestCase):
    def test_existing_viewer_role_receives_upload_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "viewer-upload.db"
            config = Config("alembic.ini")
            config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
            command.upgrade(config, "0046_asset_pipeline_fk_detach")
            engine = create_engine(f"sqlite:///{path}")
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO roles (id, tenant_id, role_key, name, description, is_system, protected, status, created_at, updated_at) "
                    "VALUES ('viewer-role', 'tenant-a', 'viewer', 'Viewer', 'Old viewer', 1, 1, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ))
            engine.dispose()

            command.upgrade(config, "head")
            engine = create_engine(f"sqlite:///{path}")
            with engine.connect() as connection:
                granted = connection.execute(text(
                    "SELECT COUNT(*) FROM role_permissions rp "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = 'viewer-role' AND p.permission_key = 'assets.upload'"
                )).scalar_one()
            self.assertEqual(granted, 1)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
