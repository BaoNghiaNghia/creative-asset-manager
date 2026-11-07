"""Narrow additive-schema rollback compatibility, tested without production DB.

The previous application schema head is 0202_etsy_spy_later. The only allowed
forward version is 0203_cam_agent_harness_store with both exact tenant-scoped
tables/foreign key present. No startup migrations or write operations.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.core.database import (
    DatabaseStartupError, validate_alembic_head, _verified_agent_harness_successor,
)
from app.modules.agent_harness.model import AgentDefinitionModel, AgentExperienceModel


@pytest.fixture(autouse=True)
def pretend_this_is_previous_0203_release(monkeypatch):
    # The old 0203 test verifies the 0202->0203 contract; newer releases
    # have HEAD 0204 but must retain this earlier rollback contract.
    monkeypatch.setattr(
        "app.core.database.expected_alembic_head",
        lambda url: "0203_cam_agent_harness_store",
    )


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


def test_new_release_rejects_old_0202_revision_before_migration() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, "0202_etsy_spy_later")
        with pytest.raises(DatabaseStartupError, match="not at Alembic head"):
            validate_alembic_head(engine, database_url="sqlite:///:memory:")
    finally:
        engine.dispose()


def test_exact_0203_schema_accepted_and_still_rollback_compatible() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        _revision(engine, "0203_cam_agent_harness_store")
        _complete_agent_schema(engine)
        assert validate_alembic_head(engine, database_url="sqlite:///:memory:") == "0203_cam_agent_harness_store"
        with engine.connect() as connection:
            assert _verified_agent_harness_successor(
                connection, "0202_etsy_spy_later", ("0203_cam_agent_harness_store",),
            )
            assert not _verified_agent_harness_successor(
                connection, "0202_etsy_spy_later", ("0204_unreviewed_change",),
            )
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
