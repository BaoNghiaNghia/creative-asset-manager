from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


class DatabaseStartupError(RuntimeError):
    """Raised when the configured database is unavailable or has the wrong schema."""


_api_root = Path(__file__).resolve().parents[2]
_default_database = f"sqlite:///{(_api_root / 'data' / 'creative_asset_manager.db').as_posix()}"


def resolve_database_url(settings: Settings) -> str:
    return settings.DATABASE_URL or _default_database


def create_database_engine(settings: Settings) -> Engine:
    database_url = resolve_database_url(settings)
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        (_api_root / "data").mkdir(parents=True, exist_ok=True)

    is_memory_database = database_url in {"sqlite://", "sqlite:///:memory:"}
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if is_sqlite:
        engine_options["connect_args"] = {"check_same_thread": False}
        if is_memory_database:
            engine_options["poolclass"] = StaticPool
    else:
        engine_options.update(
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_timeout=settings.DATABASE_POOL_TIMEOUT_SECONDS,
            pool_recycle=settings.DATABASE_POOL_RECYCLE_SECONDS,
        )
        if database_url.startswith("postgresql"):
            engine_options["connect_args"] = {
                "connect_timeout": settings.DATABASE_CONNECT_TIMEOUT_SECONDS
            }

    return create_engine(database_url, **engine_options)


_runtime_settings = get_settings()
DATABASE_URL = resolve_database_url(_runtime_settings)
engine = create_database_engine(_runtime_settings)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_api_root / "alembic.ini"))
    config.attributes["database_url"] = database_url
    # Embedded migrations must not replace Uvicorn's handlers. Otherwise a
    # healthy local API appears to stop at the final Alembic log line.
    config.attributes["configure_logger"] = False
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def validate_database_connection(database_engine: Engine = engine) -> None:
    try:
        with database_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise DatabaseStartupError("Database connection validation failed") from exc


def expected_alembic_head(database_url: str) -> str:
    heads = ScriptDirectory.from_config(_alembic_config(database_url)).get_heads()
    if len(heads) != 1:
        raise DatabaseStartupError(
            f"Expected exactly one Alembic head, found {len(heads)}"
        )
    return heads[0]


def _agent_harness_schema_complete(connection) -> bool:
    """Verify the exact additive Agent Harness schema before accepting 0203.

    Both the new release and the previous rollback bridge reject missing tables,
    missing required columns, and a missing tenant-bound agent-version FK.
    """
    schema = inspect(connection)
    required = {
        "cam_agent_definitions": {"tenant_id", "agent_id", "version", "capabilities_json",
                                  "allowed_tools_json", "preferred_executor", "enabled"},
        "cam_agent_experiences": {"tenant_id", "event_key", "agent_id", "agent_version",
                                  "scope_key", "rule_key", "status", "evidence_refs_json"},
    }
    try:
        if not set(required).issubset(schema.get_table_names()):
            return False
        for table, columns in required.items():
            if not columns.issubset({item["name"] for item in schema.get_columns(table)}):
                return False
        foreign_keys = schema.get_foreign_keys("cam_agent_experiences")
        return any(
            constraint.get("referred_table") == "cam_agent_definitions"
            and tuple(constraint.get("constrained_columns") or ())
                == ("tenant_id", "agent_id", "agent_version")
            and tuple(constraint.get("referred_columns") or ())
                == ("tenant_id", "agent_id", "version")
            for constraint in foreign_keys
        )
    except SQLAlchemyError:
        return False


def _verified_agent_harness_successor(connection, expected_head: str, current_heads: tuple[str, ...]) -> bool:
    """A previous 0202 backend may roll back after only this reviewed migration."""
    return (
        expected_head == "0202_etsy_spy_later"
        and current_heads == ("0203_cam_agent_harness_store",)
        and _agent_harness_schema_complete(connection)
    )


def _holdout_study_schema_complete(connection) -> bool:
    """Rollback-safe structural gate for future additive holdout revision 0204.

    Only an intact tenant-scoped trial + frozen keyword-assignment schema may
    be accepted by the preceding 0203 release. No migrations are run here.
    """
    schema = inspect(connection)
    required = {
        "cam_agent_trials": {
            "tenant_id", "study_id", "cohort", "candidate_event_key",
            "agent_version", "trial_version", "holdout_percent", "status",
            "registered_by", "registered_at",
        },
        "cam_agent_trial_assignments": {
            "tenant_id", "study_id", "keyword_id", "keyword_sha256",
            "arm", "assigned_at",
        },
    }
    try:
        if not set(required).issubset(schema.get_table_names()):
            return False
        for table, columns in required.items():
            if not columns.issubset({item["name"] for item in schema.get_columns(table)}):
                return False
        trial_keys = schema.get_foreign_keys("cam_agent_trials")
        assignment_keys = schema.get_foreign_keys("cam_agent_trial_assignments")
        if not any(
            key.get("referred_table") == "cam_agent_experiences"
            and key.get("constrained_columns") == ["tenant_id", "candidate_event_key"]
            and key.get("referred_columns") == ["tenant_id", "event_key"]
            for key in trial_keys
        ):
            return False
        if not any(
            key.get("referred_table") == "cam_agent_trials"
            and key.get("constrained_columns") == ["tenant_id", "study_id"]
            and key.get("referred_columns") == ["tenant_id", "study_id"]
            for key in assignment_keys
        ):
            return False
        unique = schema.get_unique_constraints("cam_agent_trial_assignments")
        return any(
            constraint.get("column_names") == ["tenant_id", "keyword_id"]
            for constraint in unique
        )
    except SQLAlchemyError:
        return False


def _verified_holdout_successor(connection, expected_head: str, current_heads: tuple[str, ...]) -> bool:
    """The preceding 0203 release may start against reviewed additive 0204."""
    return (
        expected_head == "0203_cam_agent_harness_store"
        and current_heads == ("0204_cam_agent_holdout_studies",)
        and _agent_harness_schema_complete(connection)
        and _holdout_study_schema_complete(connection)
    )


def _trial_evidence_schema_complete(connection) -> bool:
    """Validate the exact additive 0205 exposure/QA ledger before rollback.

    Neither existence of these tables nor successful migration constitutes a
    real trial, applied challenger treatment or independent QA verification.
    The 0204 release may start against 0205 only with the reviewed FK,
    uniqueness, immutable-identity and restricted-verdict constraints intact.
    """
    schema = inspect(connection)
    required = {
        "cam_agent_trial_exposures": {
            "tenant_id", "study_id", "keyword_id", "processing_job_id",
            "arm", "policy_version", "policy_sha256", "worker_identity",
            "applied_at",
        },
        "cam_agent_trial_qa_reviews": {
            "tenant_id", "review_id", "study_id", "keyword_id",
            "output_version_id", "artifact_sha256", "blind_token_sha256",
            "reviewer_id", "verdict", "critical_defect", "reviewed_at",
        },
    }
    try:
        if not set(required).issubset(set(schema.get_table_names())):
            return False
        for table, cols in required.items():
            if not cols.issubset({item["name"] for item in schema.get_columns(table)}):
                return False
        if (schema.get_pk_constraint("cam_agent_trial_exposures").get("constrained_columns")
                != ["tenant_id", "study_id", "keyword_id"]):
            return False
        if (schema.get_pk_constraint("cam_agent_trial_qa_reviews").get("constrained_columns")
                != ["tenant_id", "review_id"]):
            return False

        def has_fk(table, referred, source, target):
            return any(
                row.get("referred_table") == referred
                and row.get("constrained_columns") == list(source)
                and row.get("referred_columns") == list(target)
                for row in schema.get_foreign_keys(table)
            )

        if not has_fk(
            "cam_agent_trial_exposures", "cam_agent_trial_assignments",
            ("tenant_id", "study_id", "keyword_id"),
            ("tenant_id", "study_id", "keyword_id"),
        ) or not has_fk(
            "cam_agent_trial_exposures", "processing_jobs",
            ("processing_job_id",), ("id",),
        ) or not has_fk(
            "cam_agent_trial_qa_reviews", "cam_agent_trial_exposures",
            ("tenant_id", "study_id", "keyword_id"),
            ("tenant_id", "study_id", "keyword_id"),
        ) or not has_fk(
            "cam_agent_trial_qa_reviews", "rrugc_image_output_versions",
            ("output_version_id",), ("id",),
        ):
            return False

        def has_unique(table, columns):
            return any(
                tuple(row.get("column_names") or ()) == tuple(columns)
                for row in schema.get_unique_constraints(table)
            )

        if not has_unique(
            "cam_agent_trial_exposures", ("tenant_id", "processing_job_id"),
        ) or not has_unique(
            "cam_agent_trial_qa_reviews", ("tenant_id", "output_version_id"),
        ):
            return False
        def checked(table: str, name: str, column: str, *labels: str) -> bool:
            # PostgreSQL rewrites CHECK (column IN (...)) as
            # (column)::text = ANY ((ARRAY[...])::text[]). SQLite preserves IN.
            # Retain the exact named constraint and supported enum members;
            # never accept a permissive CHECK (1 = 1) with the same name.
            for row in schema.get_check_constraints(table):
                if row.get("name") != name:
                    continue
                body = "".join(str(row.get("sqltext") or "").lower().split())
                body = body.replace('"', "").replace("`", "")
                if column not in body or any(label not in body for label in labels):
                    return False
                if name == "ck_cam_trial_qa_critical":
                    # The generated PG expression may use De Morgan
                    # normalization; both NOT(...) and NOT col OR <> qualify.
                    return ("not" in body or "<>" in body)
                return ("in(" in body or "=any(" in body)
            return False

        return (
            checked(
                "cam_agent_trial_exposures", "ck_cam_trial_exposure_arm",
                "arm", "'control'", "'challenger'",
            )
            and checked(
                "cam_agent_trial_qa_reviews", "ck_cam_trial_qa_verdict",
                "verdict", "'accepted'", "'rejected'",
            )
            and checked(
                "cam_agent_trial_qa_reviews", "ck_cam_trial_qa_critical",
                "critical_defect", "verdict", "'accepted'",
            )
        )
    except SQLAlchemyError:
        return False


def _verified_trial_evidence_successor(connection, expected_head: str, current_heads: tuple[str, ...]) -> bool:
    """0204 can only roll back across the one exact 0205 additive revision."""
    return (
        expected_head == "0204_cam_agent_holdout_studies"
        and current_heads == ("0205_cam_agent_trial_evidence",)
        and _agent_harness_schema_complete(connection)
        and _holdout_study_schema_complete(connection)
        and _trial_evidence_schema_complete(connection)
    )


def validate_alembic_head(
    database_engine: Engine = engine,
    *,
    database_url: str = DATABASE_URL,
) -> str:
    expected_head = expected_alembic_head(database_url)
    try:
        with database_engine.connect() as connection:
            current_heads = MigrationContext.configure(connection).get_current_heads()
            approved_successor = (
                _verified_agent_harness_successor(connection, expected_head, tuple(current_heads))
                or _verified_holdout_successor(connection, expected_head, tuple(current_heads))
                or _verified_trial_evidence_successor(connection, expected_head, tuple(current_heads))
            ) if tuple(current_heads) != (expected_head,) else False
            exact_agent_schema_valid = (
                _agent_harness_schema_complete(connection)
                if expected_head == "0203_cam_agent_harness_store"
                and tuple(current_heads) == (expected_head,)
                else True
            )
            exact_holdout_schema_valid = (
                _agent_harness_schema_complete(connection)
                and _holdout_study_schema_complete(connection)
                if expected_head == "0204_cam_agent_holdout_studies"
                and tuple(current_heads) == (expected_head,)
                else True
            )
            exact_trial_evidence_valid = (
                _agent_harness_schema_complete(connection)
                and _holdout_study_schema_complete(connection)
                and _trial_evidence_schema_complete(connection)
                if expected_head == "0205_cam_agent_trial_evidence"
                and tuple(current_heads) == (expected_head,)
                else True
            )
    except SQLAlchemyError as exc:
        raise DatabaseStartupError("Could not read the Alembic revision") from exc
    if not exact_agent_schema_valid:
        raise DatabaseStartupError("Agent Harness schema incomplete at revision 0203")
    if not exact_holdout_schema_valid:
        raise DatabaseStartupError("Agent Harness holdout schema incomplete at revision 0204")
    if not exact_trial_evidence_valid:
        raise DatabaseStartupError("Agent Harness evidence schema incomplete at revision 0205")
    if tuple(current_heads) != (expected_head,) and not approved_successor:
        current = ", ".join(current_heads) if current_heads else "unversioned"
        raise DatabaseStartupError(
            f"Database schema is not at Alembic head: current={current}, "
            f"expected={expected_head}"
        )
    return expected_head


def upgrade_development_database(
    database_engine: Engine = engine,
    *,
    database_url: str = DATABASE_URL,
) -> None:
    config = _alembic_config(database_url)
    with database_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def init_database(
    settings: Settings | None = None,
    *,
    database_engine: Engine | None = None,
) -> None:
    runtime_settings = settings or get_settings()
    selected_engine = database_engine or engine
    database_url = resolve_database_url(runtime_settings)

    validate_database_connection(selected_engine)
    if runtime_settings.is_production:
        validate_alembic_head(selected_engine, database_url=database_url)
        return

    upgrade_development_database(selected_engine, database_url=database_url)
    validate_alembic_head(selected_engine, database_url=database_url)


def dispose_database(database_engine: Engine | None = None) -> None:
    (database_engine or engine).dispose()
