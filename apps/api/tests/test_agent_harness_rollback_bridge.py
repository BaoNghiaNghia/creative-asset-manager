"""Narrow additive-schema rollback compatibility, tested without production DB.

The previous application schema head is 0202_etsy_spy_later. The only allowed
forward version is 0203_cam_agent_harness_store with both exact tenant-scoped
tables/foreign key present. No startup migrations or write operations.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.core.database import (
    DatabaseStartupError, validate_alembic_head,
)
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel


def _revision(engine, revision: str) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) PRIMARY KEY)"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )


def _complete_agent_schema(engine) -> None:
    AgentDefinitionModel.__table__.create(engine)
    AgentExperienceModel.__table__.create(engine)


def test_current_0202_revision_remains_accepted() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, "0202_etsy_spy_later")
        assert validate_alembic_head(engine, database_url="sqlite:///:memory:") == "0202_etsy_spy_later"
    finally:
        engine.dispose()


def test_exact_additive_0203_schema_allows_prior_release_to_start() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, "0203_cam_agent_harness_store")
        _complete_agent_schema(engine)
        assert validate_alembic_head(engine, database_url="sqlite:///:memory:") == "0202_etsy_spy_later"
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", [
    "0201_etsy_spy_parent_keywords", "0203_some_other_change",
    "0204_unreviewed_change", "totally_unknown",
])
def test_unknown_downgraded_or_unreviewed_revision_fails(revision: str) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, revision)
        _complete_agent_schema(engine)
        with pytest.raises(DatabaseStartupError, match="not at Alembic head"):
            validate_alembic_head(engine, database_url="sqlite:///:memory:")
    finally:
        engine.dispose()


def test_partial_0203_without_required_tables_fails() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, "0203_cam_agent_harness_store")
        AgentDefinitionModel.__table__.create(engine)
        with pytest.raises(DatabaseStartupError):
            validate_alembic_head(engine, database_url="sqlite:///:memory:")
    finally:
        engine.dispose()


def test_partial_0203_without_foreign_key_fails() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, "0203_cam_agent_harness_store")
        AgentDefinitionModel.__table__.create(engine)
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE cam_agent_experiences ("
                "tenant_id VARCHAR(255) NOT NULL,"
                "event_key VARCHAR(64) NOT NULL,"
                "agent_id VARCHAR(96) NOT NULL,"
                "agent_version VARCHAR(20) NOT NULL,"
                "scope_key VARCHAR(160) NOT NULL,"
                "rule_key VARCHAR(160) NOT NULL,"
                "status VARCHAR(16) NOT NULL,"
                "evidence_refs_json TEXT NOT NULL,"
                "PRIMARY KEY (tenant_id,event_key)"
                ")"
            ))
        with pytest.raises(DatabaseStartupError):
            validate_alembic_head(engine, database_url="sqlite:///:memory:")
    finally:
        engine.dispose()
