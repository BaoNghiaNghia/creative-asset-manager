import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


_api_root = Path(__file__).resolve().parents[2]
_default_database = f"sqlite:///{(_api_root / 'data' / 'creative_asset_manager.db').as_posix()}"
DATABASE_URL = os.getenv("DATABASE_URL") or _default_database

if DATABASE_URL.startswith("sqlite"):
    (_api_root / "data").mkdir(parents=True, exist_ok=True)

_is_memory_database = DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
    poolclass=StaticPool if _is_memory_database else None,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def init_database() -> None:
    from app.modules.ai_batch import model as ai_batch_model  # noqa: F401
    from app.modules.ai_governance import model as ai_governance_model  # noqa: F401
    from app.modules.ai_metadata import model as ai_metadata_model  # noqa: F401
    from app.modules.auth_persistence import model as auth_persistence_model  # noqa: F401
    from app.modules.assets import model as asset_model  # noqa: F401
    from app.modules.processing import model as processing_model  # noqa: F401
    from app.modules.processing_policy import model as processing_policy_model  # noqa: F401
    from app.modules.search import operations_model as search_operation_model  # noqa: F401
    from app.modules.external_ingestion import model as external_ingestion_model  # noqa: F401
    from app.modules.metadata import model as metadata_model  # noqa: F401
    from app.modules.storage import model as storage_model  # noqa: F401
    from app.modules.pipeline import model as pipeline_model  # noqa: F401
    from app.modules.tag import model as tag_model  # noqa: F401

    Base.metadata.create_all(bind=engine)

    from app.modules.tag.repository import TagRepository

    with SessionLocal.begin() as session:
        TagRepository(session).seed_system_tags()
