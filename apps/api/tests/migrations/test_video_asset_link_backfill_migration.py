from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


API_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = API_ROOT / "alembic.ini"


def test_0091_repairs_unlinked_video_with_provider_sha256(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'video-link-backfill.sqlite'}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0090_creative_pipeline_gpt_skills")

    engine = create_engine(url)
    checksum = "a" * 64
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
                 size_bytes, provider_checksum, provider_version, source_metadata,
                 parent_external_id, is_folder, created_at, updated_at)
                VALUES
                ('video-a', 'tenant-a', 'source-a', 'drive-video-a', 'clip.mp4',
                 'application/octet-stream', 1234, :checksum, 'v1', '{"parents":["folder-a"]}',
                 'folder-a', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {"checksum": checksum},
        )
    engine.dispose()

    command.upgrade(config, "0091_video_asset_link_backfill")

    engine = create_engine(url)
    with engine.connect() as connection:
        link = connection.execute(text(
            """
            SELECT a.content_hash, asl.source_asset_id
            FROM asset_source_links AS asl
            JOIN assets AS a
              ON a.tenant_id = asl.tenant_id
             AND a.id = asl.asset_id
            WHERE asl.tenant_id = 'tenant-a'
              AND asl.source_asset_id = 'video-a'
            """
        )).one()
        hashed = connection.execute(text(
            """
            SELECT hashed_provider_checksum, hashed_provider_version
            FROM source_assets
            WHERE tenant_id = 'tenant-a' AND id = 'video-a'
            """
        )).one()
    engine.dispose()

    assert link == (checksum, "video-a")
    assert hashed == (checksum, "v1")
