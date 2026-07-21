from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import (
    Base,
    DatabaseStartupError,
    create_database_engine,
    dispose_database,
    init_database,
    validate_alembic_head,
    validate_database_connection,
)
from app.modules.tag.model import TagModel
from app.operations.tag_cli import seed_system_tags


PRODUCTION_SETTINGS = {
    "APP_ENV": "production",
    "PUBLIC_APP_URL": "https://assets.example.com",
    "CORS_ALLOWED_ORIGINS": "https://assets.example.com",
    "TRUSTED_HOSTS": "api.example.com",
    "API_DOCS_ENABLED": False,
    "DATABASE_URL": "postgresql+psycopg://cam:test@db/cam",
}


class DatabaseStartupTest(unittest.TestCase):
    def test_production_requires_non_sqlite_database_url(self) -> None:
        missing = {**PRODUCTION_SETTINGS, "DATABASE_URL": None}
        sqlite = {**PRODUCTION_SETTINGS, "DATABASE_URL": "sqlite:///production.db"}
        with self.assertRaises(ValueError):
            Settings(**missing)
        with self.assertRaises(ValueError):
            Settings(**sqlite)

    def test_invalid_pool_settings_fail_configuration(self) -> None:
        for setting, value in (
            ("DATABASE_POOL_SIZE", 0),
            ("DATABASE_MAX_OVERFLOW", -1),
            ("DATABASE_POOL_TIMEOUT_SECONDS", 0),
            ("DATABASE_POOL_RECYCLE_SECONDS", 0),
            ("DATABASE_CONNECT_TIMEOUT_SECONDS", 0),
        ):
            with self.subTest(setting=setting):
                with self.assertRaises(ValueError):
                    Settings(**{setting: value})

    def test_postgresql_pool_settings_are_applied(self) -> None:
        settings = Settings(
            DATABASE_URL="postgresql+psycopg://cam:test@db/cam",
            DATABASE_POOL_SIZE=7,
            DATABASE_MAX_OVERFLOW=4,
            DATABASE_POOL_TIMEOUT_SECONDS=12,
            DATABASE_POOL_RECYCLE_SECONDS=900,
            DATABASE_CONNECT_TIMEOUT_SECONDS=3,
        )
        database_engine = create_database_engine(settings)
        try:
            self.assertEqual(database_engine.pool.size(), 7)
            self.assertEqual(database_engine.pool._max_overflow, 4)
            self.assertEqual(database_engine.pool._timeout, 12)
            self.assertEqual(database_engine.pool._recycle, 900)
        finally:
            database_engine.dispose()

    def test_connection_validation_and_disposal(self) -> None:
        database_engine = create_engine("sqlite:///:memory:")
        validate_database_connection(database_engine)
        dispose_database(database_engine)

        broken_engine = MagicMock()
        broken_engine.connect.side_effect = OperationalError(
            "SELECT 1", {}, RuntimeError("offline")
        )
        with self.assertRaises(DatabaseStartupError):
            validate_database_connection(broken_engine)

        mocked_engine = MagicMock()
        dispose_database(mocked_engine)
        mocked_engine.dispose.assert_called_once_with()

    def test_unversioned_schema_fails_head_validation(self) -> None:
        database_engine = create_engine("sqlite:///:memory:")
        try:
            with self.assertRaisesRegex(DatabaseStartupError, "unversioned"):
                validate_alembic_head(
                    database_engine,
                    database_url="sqlite:///:memory:",
                )
        finally:
            database_engine.dispose()

    def test_development_startup_uses_alembic_and_does_not_seed_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "development.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            settings = Settings(DATABASE_URL=database_url)
            database_engine = create_database_engine(settings)
            try:
                init_database(settings, database_engine=database_engine)
                with database_engine.connect() as connection:
                    revision = connection.exec_driver_sql(
                        "SELECT version_num FROM alembic_version"
                    ).scalar_one()
                    tag_count = connection.execute(
                        select(func.count()).select_from(TagModel)
                    ).scalar_one()
                self.assertTrue(revision)
                self.assertEqual(tag_count, 0)
            finally:
                database_engine.dispose()

    def test_in_memory_development_uses_the_validated_connection(self) -> None:
        settings = Settings(DATABASE_URL="sqlite:///:memory:")
        database_engine = create_database_engine(settings)
        try:
            init_database(settings, database_engine=database_engine)
            self.assertTrue(
                validate_alembic_head(
                    database_engine,
                    database_url="sqlite:///:memory:",
                )
            )
        finally:
            database_engine.dispose()

    def test_production_startup_never_runs_migrations_or_create_all(self) -> None:
        settings = Settings(**PRODUCTION_SETTINGS)
        mocked_engine = MagicMock()
        with (
            patch("app.core.database.validate_database_connection") as connection,
            patch("app.core.database.validate_alembic_head") as head,
            patch("app.core.database.upgrade_development_database") as upgrade,
            patch.object(Base.metadata, "create_all") as create_all,
        ):
            init_database(settings, database_engine=mocked_engine)
        connection.assert_called_once_with(mocked_engine)
        head.assert_called_once_with(
            mocked_engine,
            database_url=PRODUCTION_SETTINGS["DATABASE_URL"],
        )
        upgrade.assert_not_called()
        create_all.assert_not_called()

    def test_system_tag_seed_command_is_idempotent(self) -> None:
        database_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(database_engine, tables=[TagModel.__table__])
        sessions = sessionmaker(database_engine, expire_on_commit=False)
        try:
            first = seed_system_tags(sessions)
            second = seed_system_tags(sessions)
            with sessions() as session:
                count = session.scalar(select(func.count()).select_from(TagModel))
            self.assertEqual(first, second)
            self.assertEqual(count, 2)
        finally:
            database_engine.dispose()


if __name__ == "__main__":
    unittest.main()
