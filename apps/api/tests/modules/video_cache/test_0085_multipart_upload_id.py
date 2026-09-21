from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Text, create_engine, inspect, text

from app.modules.video_cache.model import VideoCacheObjectModel


def test_model_uses_unbounded_text_for_multipart_upload_id():
    column_type = VideoCacheObjectModel.__table__.c.multipart_upload_id.type
    assert isinstance(column_type, Text)


def test_0085_is_the_single_alembic_head():
    assert ScriptDirectory.from_config(Config("alembic.ini")).get_heads() == [
        "0085_r2_multipart_upload_id_text"
    ]


def test_0085_sqlite_upgrade_downgrade_upgrade_round_trip(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'multipart-id.sqlite'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "0084_video_cdn_delivery_runtime")
    command.upgrade(config, "0085_r2_multipart_upload_id_text")

    engine = create_engine(url)
    column = next(
        item
        for item in inspect(engine).get_columns("video_cache_objects")
        if item["name"] == "multipart_upload_id"
    )
    assert isinstance(column["type"], Text)
    engine.dispose()

    command.downgrade(config, "0084_video_cdn_delivery_runtime")
    command.upgrade(config, "0085_r2_multipart_upload_id_text")


def test_0085_downgrade_rejects_long_existing_upload_id(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'long-multipart-id.sqlite'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0085_r2_multipart_upload_id_text")

    engine = create_engine(url)
    with engine.begin() as connection:
        tenant_id = "migration-test-tenant"
        connection.execute(
            text(
                "INSERT INTO tenants "
                "(id, name, slug, status, created_at, updated_at) "
                "VALUES (:id, :name, :slug, 'active', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": tenant_id,
                "name": "Migration test tenant",
                "slug": "migration-test-tenant",
            },
        )
        connection.execute(
            text(
                "INSERT INTO video_cache_objects "
                "(id, tenant_id, asset_id, source_asset_id, content_hash, r2_key, "
                "mime_type, size_bytes, reserved_bytes, status, attempt_count, "
                "created_at, updated_at, fill_generation, multipart_upload_id) "
                "VALUES (:id, :tenant_id, :asset_id, :source_asset_id, :hash, "
                ":r2_key, 'video/mp4', 1, 1, 'preparing', 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, 0, :upload_id)"
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": tenant_id,
                "asset_id": "00000000-0000-0000-0000-000000000002",
                "source_asset_id": "00000000-0000-0000-0000-000000000003",
                "hash": "a" * 64,
                "r2_key": "migration-test/video.mp4",
                "upload_id": "x" * 343,
            },
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="longer R2 upload IDs exist"):
        command.downgrade(config, "0084_video_cdn_delivery_runtime")
