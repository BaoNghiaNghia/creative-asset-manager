from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


API_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = API_ROOT / "alembic.ini"


def test_0092_adds_unique_source_asset_link_constraint(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'asset-source-link-invariant.sqlite'}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0091_video_asset_link_backfill")
    command.upgrade(config, "0092_asset_source_link_invariant")

    engine = create_engine(url)
    names = {
        constraint["name"]
        for constraint in inspect(engine).get_unique_constraints("asset_source_links")
    }
    engine.dispose()

    assert "uq_asset_source_links_tenant_source_asset" in names


def test_0092_repairs_duplicate_link_when_sha256_identifies_canonical_asset(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'asset-source-link-repair.sqlite'}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0091_video_asset_link_backfill")

    canonical_hash = "a" * 64
    stale_hash = "b" * 64
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
                 size_bytes, provider_checksum, source_metadata, is_folder,
                 created_at, updated_at)
                VALUES
                ('source-asset-a', 'tenant-a', 'source-a', 'drive-a', 'image.jpg',
                 'image/jpeg', 123, :checksum, '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {"checksum": canonical_hash},
        )
        for asset_id, content_hash in (
            ("asset-canonical", canonical_hash),
            ("asset-stale", stale_hash),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO assets
                    (id, tenant_id, content_hash, mime_type, size_bytes, created_at, updated_at)
                    VALUES
                    (:id, 'tenant-a', :content_hash, 'image/jpeg', 123,
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"id": asset_id, "content_hash": content_hash},
            )
        connection.execute(text(
            """
            INSERT INTO asset_source_links
            (id, tenant_id, asset_id, source_asset_id, created_at)
            VALUES
            ('link-canonical', 'tenant-a', 'asset-canonical', 'source-asset-a', CURRENT_TIMESTAMP),
            ('link-stale', 'tenant-a', 'asset-stale', 'source-asset-a', CURRENT_TIMESTAMP)
            """
        ))
    engine.dispose()

    command.upgrade(config, "0092_asset_source_link_invariant")

    engine = create_engine(url)
    with engine.connect() as connection:
        rows = connection.execute(text(
            """
            SELECT asl.asset_id
            FROM asset_source_links AS asl
            WHERE asl.tenant_id = 'tenant-a'
              AND asl.source_asset_id = 'source-asset-a'
            """
        )).scalars().all()
    engine.dispose()

    assert rows == ["asset-canonical"]
