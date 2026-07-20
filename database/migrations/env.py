from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.database import Base
from app.modules.processing import model as processing_models  # noqa: F401
from app.modules.assets import model as asset_models  # noqa: F401
from app.modules.storage import model as storage_models  # noqa: F401
from app.modules.external_ingestion import model as external_ingestion_models  # noqa: F401
from app.modules.search import operations_model as search_operation_models  # noqa: F401
from app.modules.ai_metadata import model as ai_metadata_models  # noqa: F401
from app.modules.metadata import model as metadata_models  # noqa: F401
from app.modules.tag import model as tag_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
