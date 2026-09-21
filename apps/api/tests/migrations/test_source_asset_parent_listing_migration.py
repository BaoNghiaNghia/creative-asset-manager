from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Boolean, String, create_engine, inspect, text


API_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = API_ROOT / "alembic.ini"


def test_0086_backfills_parent_listing_fields_and_index(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'parent-listing.sqlite'}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0085_r2_multipart_upload_id_text")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            """
            INSERT INTO external_sources
            (id, tenant_id, source_key, source_type, display_name, source_metadata,
             oauth_connection_id, status, created_at, updated_at)
            VALUES
            ('source-a', 'tenant-a', 'source-key', 'google_drive', 'Drive', '{}',
             NULL, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ))
        connection.execute(
            text(
                """
                INSERT INTO source_assets
                (id, tenant_id, external_source_id, external_asset_id, filename, mime_type,
                 source_metadata, created_at, updated_at)
                VALUES
                ('folder-child', 'tenant-a', 'source-a', 'folder-child', 'Folder',
                 'application/vnd.google-apps.folder',
                 :source_metadata,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {"source_metadata": '{"parents":["root"],"is_folder":true}'},
        )
        connection.execute(text(
            """
            INSERT INTO source_assets
            (id, tenant_id, external_source_id, external_asset_id, filename, mime_type,
             source_metadata, created_at, updated_at)
            VALUES
            ('image-child', 'tenant-a', 'source-a', 'image-child', 'Image.jpg',
             'image/jpeg', '{"parent_id":"root"}',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ))
    engine.dispose()

    command.upgrade(config, "0086_source_asset_parent_listing")

    engine = create_engine(url)
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("source_assets")}
    assert isinstance(columns["parent_external_id"]["type"], String)
    assert isinstance(columns["is_folder"]["type"], Boolean)
    assert "ix_source_assets_parent_listing" in {
        index["name"] for index in inspector.get_indexes("source_assets")
    }

    with engine.connect() as connection:
        rows = connection.execute(text(
            """
            SELECT id, parent_external_id, is_folder
            FROM source_assets
            WHERE id IN ('folder-child', 'image-child')
            ORDER BY id
            """
        )).all()
    engine.dispose()

    assert rows == [
        ("folder-child", "root", True),
        ("image-child", "root", False),
    ]
